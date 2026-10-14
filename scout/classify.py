"""Sort an open, unassigned issue into a bucket by reading its timeline.

free    - no PR, no claim (in a comment, or the author's own plan in the description), no blocking label
quiet   - someone claimed it N+ days ago and never opened a PR
blocked - waiting on author / code owners / semconv / discussion
taken   - has an open or manually linked PR, or a recent claim
mine    - you already claimed it
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# Offers to *review* or *test* ("happy to help review", "I'll open a separate issue") are not claims;
# counting them hid real free issues as taken.
CLAIM_RE = re.compile(
    r"i('d| would) like to (work|take|pick|help(?! (?:review|test)))|i can (take(?! a (?:look|peek))|work|pick)"
    r"|i('ll| will) (take(?! a (?:look|peek))|work|pick|open a (?:pr|pull request)"
    r"|send a (?:pr|pull request|fix|patch))"
    r"|happy to (pick|take|work|contribute|help(?! (?:review|test)))"
    r"|working on (this|it)|assign (this|it)? ?to me|can i (take|work|pick)"
    r"|let me (take|work|pick)|i('m| am) (working|taking|picking)"
    r"|i have (opened|raised|submitted) a pr",
    re.IGNORECASE,
)
ASSIGN_RE = re.compile(r"^\s*/assign\b", re.MULTILINE)   # Kubernetes (Prow) claim command

# In an issue's own description, the author saying they'll build it ("I would like to add an
# opt-in option...", "I'm happy to implement this") is a claim, just like a comment would be.
AUTHOR_INTENT_RE = re.compile(
    r"\bi('d| would) like to (add|implement|contribute|introduce|submit|build)\b"
    r"|\bi('m| am) (happy|willing|planning|going) to (implement|contribute|add|submit|open a (pr|pull request))"
    r"|\bi('ll| will| can) (implement|contribute|submit|open a (pr|pull request)|send a (pr|pull request))\b"
    r"|\bi('ve| have) (started|begun) (working|a (draft|prototype|pr))",
    re.IGNORECASE,
)
BOT_RE = re.compile(r"\[bot\]$|^github-actions|bot$|dashboard", re.IGNORECASE)

DEFAULT_BLOCKING_LABELS = (
    "waiting for author",
    "waiting-for:semantic-conventions",
    "waiting-for-code-owners",
    "discussion needed",
    "needs discussion",
)


@dataclass
class Candidate:
    repo: str
    number: int
    title: str
    url: str
    labels: list
    updated_at: str
    author: str
    component: str
    bucket: str = ""
    reason: str = ""
    body: str = ""
    created_at: str = ""

    @property
    def key(self):
        return f"{self.repo}#{self.number}"

    @classmethod
    def from_issue(cls, repo, issue, component):
        return cls(
            repo=repo,
            number=issue["number"],
            title=issue.get("title", ""),
            url=issue.get("html_url", ""),
            labels=[lbl["name"] for lbl in issue.get("labels", [])],
            updated_at=issue.get("updated_at", ""),
            author=(issue.get("user") or {}).get("login", ""),
            component=component,
            body=issue.get("body") or "",
            created_at=issue.get("created_at", ""),
        )


def parse_time(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def days_between(earlier, now):
    return max(0, (now - parse_time(earlier)).days)


def is_bot(login):
    return bool(BOT_RE.search(login or ""))


def actor_of(event):
    who = event.get("actor") or event.get("user") or {}
    return who.get("login", "")


def pr_ref(source_issue, home_repo):
    """'#123' for the same repo, 'owner/repo#123' for cross-repo PRs."""
    repo = (source_issue.get("repository") or {}).get("full_name")
    if not repo:
        repo_url = source_issue.get("repository_url", "")
        repo = "/".join(repo_url.rstrip("/").split("/")[-2:]) if repo_url else home_repo
    number = source_issue.get("number")
    return f"#{number}" if repo == home_repo else f"{repo}#{number}"


def classify(cand, timeline, *, user, now, quiet_days=45, blocking_labels=DEFAULT_BLOCKING_LABELS):
    open_prs, merged_prs, closed_prs = [], [], []
    linked = False
    claim = None
    author = cand.author.lower()
    if author and cand.created_at and (AUTHOR_INTENT_RE.search(cand.body) or CLAIM_RE.search(cand.body)):
        claim = (cand.author, cand.created_at, "author")
    for ev in timeline:
        kind = ev.get("event")
        if kind == "cross-referenced":
            src = (ev.get("source") or {}).get("issue") or {}
            if "pull_request" not in src:
                continue
            ref = pr_ref(src, cand.repo)
            if src.get("state") == "open":
                open_prs.append(ref)
            elif (src.get("pull_request") or {}).get("merged_at"):
                merged_prs.append(ref)
            else:
                closed_prs.append(ref)
        elif kind == "connected":
            linked = True
        elif kind == "disconnected":
            linked = False
        elif kind == "commented":
            login = actor_of(ev)
            if is_bot(login):
                continue
            body = ev.get("body") or ""
            if (CLAIM_RE.search(body) or ASSIGN_RE.search(body)) and ev.get("created_at"):
                claim = (login, ev["created_at"], "comment")
            elif claim and claim[2] == "author" and login.lower() == author and ev.get("created_at"):
                claim = (login, ev["created_at"], "author")      # the author is still at it

    notes = []
    if merged_prs and not open_prs:
        notes.append(f"earlier PR merged ({' '.join(sorted(set(merged_prs)))}), may be a follow-up")
    closed_only = sorted(set(closed_prs) - set(merged_prs) - set(open_prs))
    if closed_only and not open_prs:
        notes.append(f"earlier PR closed without merging ({' '.join(closed_only)})")

    if open_prs or linked:
        refs = " ".join(sorted(set(open_prs))) or "a linked PR"
        return _set(cand, "taken", [f"open PR: {refs}"] + notes)
    if claim:
        who, when, kind = claim
        age = days_between(when, now)
        if user and who.lower() == user.lower():
            return _set(cand, "mine", [f"you claimed it {age}d ago"] + notes)
        if kind == "author":
            said = f"{who} opened it planning to build it, last active {age}d ago"
            return _set(cand, "quiet" if age >= quiet_days else "taken", [said] + notes)
        if age >= quiet_days:
            return _set(cand, "quiet", [f"claimed by {who} {age}d ago, no PR since"] + notes)
        return _set(cand, "taken", [f"claimed by {who} {age}d ago"] + notes)
    blocking = {b.lower() for b in blocking_labels}
    hits = [lbl for lbl in cand.labels if lbl.lower() in blocking]
    if hits:
        return _set(cand, "blocked", [", ".join(hits)] + notes)
    return _set(cand, "free", notes)


def _set(cand, bucket, reasons):
    cand.bucket = bucket
    cand.reason = "; ".join(r for r in reasons if r)
    return cand
