"""Sort an open, unassigned issue into a bucket by reading its timeline.

free    - no PR, no claim comment, no blocking label
quiet   - someone claimed it N+ days ago and never opened a PR
blocked - waiting on author / code owners / semconv / discussion
taken   - has an open or manually linked PR, or a recent claim
mine    - you already claimed it
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

CLAIM_RE = re.compile(
    r"i('d| would) like to (work|take|pick|help)|i can (take|work|pick)"
    r"|i('ll| will) (take|work|open|pick|send)|happy to (pick|take|work|help|contribute)"
    r"|working on (this|it)|assign (this|it)? ?to me|can i (take|work|pick)"
    r"|let me (take|work|pick)|i('m| am) (working|taking|picking)"
    r"|i have (opened|raised|submitted) a pr",
    re.IGNORECASE,
)
ASSIGN_RE = re.compile(r"^\s*/assign\b", re.MULTILINE)   # Kubernetes (Prow) claim command
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
    open_prs, merged_prs = [], []
    linked = False
    claim = None
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
                claim = (login, ev["created_at"])

    notes = []
    if merged_prs and not open_prs:
        notes.append(f"earlier PR merged ({' '.join(sorted(set(merged_prs)))}), may be a follow-up")

    if open_prs or linked:
        refs = " ".join(sorted(set(open_prs))) or "a linked PR"
        return _set(cand, "taken", [f"open PR: {refs}"] + notes)
    if claim:
        who, when = claim
        age = days_between(when, now)
        if user and who.lower() == user.lower():
            return _set(cand, "mine", [f"you claimed it {age}d ago"] + notes)
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
