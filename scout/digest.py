"""Scan the configured repos and work out what changed since the last run."""

import re
from dataclasses import dataclass, field
from datetime import timedelta

from .classify import (ASSIGN_RE, CLAIM_RE, DEFAULT_BLOCKING_LABELS, Candidate, actor_of, classify,
                       days_between, is_bot, parse_time)
from . import ai
from .github import GitHubError


@dataclass
class Item:
    key: str
    url: str
    title: str
    detail: str = ""


@dataclass
class Digest:
    needs_you: list = field(default_factory=list)      # review requests, activity on your items
    to_pick: list = field(default_factory=list)        # newly free or newly quiet issues
    review_queue: list = field(default_factory=list)   # PRs in your areas nobody has reviewed
    errors: list = field(default_factory=list)
    baseline: bool = False
    counts: dict = field(default_factory=dict)
    board_free: list = field(default_factory=list)     # full current lists, for the board issue
    board_quiet: list = field(default_factory=list)
    board_queue: list = field(default_factory=list)
    board_followups: list = field(default_factory=list)  # open PRs on threads you're in, not yet reviewed
    board_my_waiting: list = field(default_factory=list)   # your PRs with no review after a week
    board_my_claims: list = field(default_factory=list)    # issues you claimed with no PR yet
    board_discovered: list = field(default_factory=list)   # free newcomer/help-wanted issues across your orgs
    discovered: list = field(default_factory=list)         # ...the new ones, for today's comment
    board_triage: list = field(default_factory=list)       # every issue opened recently in your areas
    triage: list = field(default_factory=list)             # ...the ones opened since the last run
    board_waiting: list = field(default_factory=list)      # threads where you spoke last and nobody replied
    board_aside: list = field(default_factory=list)        # free by the rules, but the AI check says not to start
    hidden: list = field(default_factory=list)             # keys you hid with /hide on the board issue

    def is_empty(self):
        return not (self.needs_you or self.to_pick or self.review_queue or self.discovered
                    or self.triage or self.errors or self.baseline)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_candidates(gh, cfg, now, errors, prs=None):
    """Every open, unassigned issue under the configured labels, classified.
    If `prs` is a dict, open PRs under the same labels are collected into it as
    key -> (repo, label, pr)."""
    seen, out = set(), []
    for repo_cfg in cfg["repos"]:
        repo = repo_cfg["repo"]
        for label in repo_cfg.get("labels") or [None]:      # no labels: watch the whole repo
            tag = label or repo.split("/")[-1]
            try:
                issues, label_prs = gh.open_items(repo, label, limit=cfg.get("max_issues_per_label", 60))
            except GitHubError as err:
                errors.append(f"{repo} label '{tag}': {err}")
                continue
            if prs is not None:
                for pr in label_prs:
                    prs.setdefault(f"{repo}#{pr['number']}", (repo, tag, pr))
            for issue in issues:
                cand = Candidate.from_issue(repo, issue, tag)
                if cand.key in seen:
                    continue
                seen.add(cand.key)
                try:
                    events = gh.timeline(repo, cand.number)
                except GitHubError as err:
                    errors.append(f"{cand.key} timeline: {err}")
                    continue
                classify(cand, events, user=cfg["github_user"], now=now,
                         quiet_days=cfg.get("quiet_days", 45),
                         blocking_labels=cfg.get("blocking_labels") or DEFAULT_BLOCKING_LABELS)
                out.append(cand)
    return out


def _repo_scope(cfg):
    """Search scope: the watched repos plus any organisations you discover in."""
    repos = [f"repo:{r['repo']}" for r in cfg["repos"]]
    orgs = [f"org:{o}" for o in (cfg.get("discover") or {}).get("orgs", [])]
    covered = {o.split(":", 1)[1].lower() for o in orgs}
    return " ".join(orgs + [r for r in repos if r.split(":", 1)[1].split("/")[0].lower() not in covered])


def review_requests(gh, cfg):
    q = f"is:open is:pr review-requested:{cfg['github_user']} {_repo_scope(cfg)}"
    return gh.search_issues(q, limit=50)


def unreviewed_prs(gh, cfg, now, prs, already_reported, errors):
    """Open, non-draft PRs in your labels, older than N days, not yours, with no review
    from anyone but the author. PRs already reported stay in the set while they are open
    without re-checking, so each run only fetches reviews for new candidates."""
    cutoff = now - timedelta(days=cfg.get("stale_review_days", 7))
    user = cfg["github_user"].lower()
    found = {}
    for key, (repo, label, pr) in prs.items():
        author = (pr.get("user") or {}).get("login", "")
        if pr.get("draft") or author.lower() == user or parse_time(pr["created_at"]) > cutoff:
            continue
        if key in already_reported:
            found[key] = (repo, label, pr)
            continue
        try:
            reviews = gh.pr_reviews(repo, pr["number"])
        except GitHubError as err:
            errors.append(f"{key} reviews: {err}")
            continue
        if not any((r.get("user") or {}).get("login", "") != author for r in reviews):
            found[key] = (repo, label, pr)
    return found


def activity_on_mine(gh, cfg, since, errors):
    """What other people did on issues/PRs you're involved in since the last run."""
    user = cfg["github_user"]
    q = f"involves:{user} updated:>={iso(since)} {_repo_scope(cfg)}"
    limit = cfg.get("activity_threads", 200)
    try:
        # Newest first: without a sort, search ranks by relevance and a cut-off drops
        # arbitrary threads, including ones with fresh reviews on your own PRs.
        hits = gh.search_issues(q, limit=limit, sort="updated")
    except GitHubError as err:
        errors.append(f"activity search: {err}")
        return []
    total = getattr(gh, "last_total_count", None)
    if total and total > len(hits):
        errors.append(f"activity search: {total} threads changed since the last run, only the "
                      f"{len(hits)} most recent were checked (raise activity_threads)")
    lines = []
    for hit in hits:
        repo = "/".join(hit["repository_url"].rstrip("/").split("/")[-2:])
        try:
            events = gh.timeline(repo, hit["number"])
        except GitHubError as err:
            errors.append(f"{repo}#{hit['number']} timeline: {err}")
            continue
        what = describe_events(events, user, since)
        if what:
            lines.append(Item(f"{repo}#{hit['number']}", hit["html_url"], hit["title"], ", ".join(what)))
    return lines


OFFER_RE = re.compile(r"\b(?:happy|glad|willing|keen) to (?:help )?review|\bi(?:'ll| will| can| could) review|"
                      r"\bwill (?:take a look|review)\b|\bping me\b", re.I)


def _repo_from(issue):
    full = (issue.get("repository") or {}).get("full_name")
    if full:
        return full
    return "/".join((issue.get("repository_url") or "").rstrip("/").split("/")[-2:])


def followups(gh, cfg, now, errors):
    """Two things from the open issues you're involved in:
    - open PRs by other people linked to them that you haven't reviewed;
    - issues you claimed some time ago without opening a PR (start, or let someone else take it).
    Returns ({key: Item}, {key: Item})."""
    user = cfg["github_user"]
    since = (now - timedelta(days=cfg.get("followup_days", 60))).strftime("%Y-%m-%d")
    q = f"involves:{user} is:issue is:open updated:>={since} {_repo_scope(cfg)}"
    try:
        threads = gh.search_issues(q, limit=cfg.get("followup_threads", 40))
    except GitHubError as err:
        errors.append(f"follow-up search: {err}")
        return {}, {}
    found, claims = {}, {}
    claim_days = cfg.get("claim_reminder_days", 14)
    for thread in threads:
        repo = _repo_from(thread)
        try:
            events = gh.timeline(repo, thread["number"])
        except GitHubError as err:
            errors.append(f"{repo}#{thread['number']} timeline: {err}")
            continue
        mine = [ev for ev in events if ev.get("event") == "commented" and actor_of(ev).lower() == user.lower()]
        offered = any(OFFER_RE.search(ev.get("body") or "") for ev in mine)
        opened = (thread.get("user") or {}).get("login", "").lower() == user.lower()
        if not (mine or opened):
            continue
        role = "you offered to review" if offered else ("your issue" if opened else "you commented")
        ref = f"#{thread['number']}"
        prs_here, connected, any_pr_ref = [], False, False
        for ev in events:
            if ev.get("event") == "connected":
                connected = True
            if ev.get("event") != "cross-referenced":
                continue
            src = (ev.get("source") or {}).get("issue") or {}
            if "pull_request" not in src:
                continue
            any_pr_ref = True
            author = (src.get("user") or {}).get("login", "")
            if src.get("state") != "open" or author.lower() == user.lower():
                continue
            if (src.get("pull_request") or {}).get("merged_at"):
                continue
            prs_here.append((_repo_from(src) or repo, src, author))
        for pr_repo, src, author in prs_here:
            key = f"{pr_repo}#{src['number']}"
            if key in found:
                continue
            try:
                reviews = gh.pr_reviews(pr_repo, src["number"])
            except GitHubError as err:
                errors.append(f"{key} reviews: {err}")
                continue
            if any((r.get("user") or {}).get("login", "").lower() == user.lower() for r in reviews):
                continue
            draft = " · draft" if src.get("draft") else ""
            found[key] = Item(key, src.get("html_url", ""), src.get("title", ""),
                              f"by {author}{draft} · linked to {ref} ({role})")
        if connected and not any_pr_ref:      # linked by hand, and GitHub didn't say which PR
            key = f"{repo}#{thread['number']}:linked"
            found.setdefault(key, Item(key, thread["html_url"], thread["title"],
                                       f"a PR was linked to this issue ({role}); open the issue to find it"))
        my_claims = [ev for ev in mine if CLAIM_RE.search(ev.get("body") or "") or ASSIGN_RE.search(ev.get("body") or "")]
        my_pr = any(ev.get("event") == "cross-referenced"
                    and ((ev.get("source") or {}).get("issue") or {}).get("user", {}).get("login", "").lower() == user.lower()
                    and "pull_request" in ((ev.get("source") or {}).get("issue") or {})
                    for ev in events)
        if my_claims and not my_pr and not opened and ev_time(my_claims[-1]):
            age = days_between(ev_time(my_claims[-1]), now)
            if age >= claim_days:
                key = f"{repo}#{thread['number']}"
                claims[key] = Item(key, thread["html_url"], thread["title"],
                                   f"you said you'd take it {age}d ago, no PR yet: post an update, or say "
                                   "you can't get to it so someone else can")
    return found, claims


def ev_time(ev):
    return ev.get("created_at") or ev.get("submitted_at") or ""


def waiting_prs(gh, cfg, now, errors):
    """Your open, non-draft PRs that nobody has reviewed after `ping_after_days`."""
    days = cfg.get("ping_after_days", 7)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    q = f"is:pr is:open draft:false review:none author:{cfg['github_user']} created:<={cutoff} {_repo_scope(cfg)}"
    try:
        hits = gh.search_issues(q, limit=30)
    except GitHubError as err:
        errors.append(f"your waiting PRs: {err}")
        return {}
    out = {}
    for pr in hits:
        key = f"{_repo_from(pr)}#{pr['number']}"
        age = days_between(pr["created_at"], now)
        out[key] = Item(key, pr["html_url"], pr["title"],
                        f"open {age}d with no review: a polite ping on the PR, or in the project's channel, is fine now")
    return out


def discover(gh, cfg, now, state, exclude, errors):
    """Issues labelled for newcomers or help across whole organisations, checked for claims
    and PRs like everything else. Timelines are re-read only for issues that changed.
    Returns ({key: Candidate}, new_cache)."""
    d = cfg.get("discover") or {}
    orgs = d.get("orgs") or []
    if not orgs:
        return {}, {}
    since = (now - timedelta(days=d.get("max_age_days", 90))).strftime("%Y-%m-%d")
    scope = " ".join(f"org:{o}" for o in orgs) + "".join(f" language:{lang}" for lang in d.get("languages", []))
    items = {}
    for label in d.get("labels") or ["good first issue", "help wanted"]:
        q = f'is:issue is:open no:assignee -linked:pr archived:false label:"{label}" created:>={since} {scope}'
        try:
            for it in gh.search_issues(q, limit=100, sort="created"):
                key = f"{_repo_from(it)}#{it['number']}"
                if key not in exclude:
                    entry = items.setdefault(key, (it, []))
                    entry[1].append(label)
        except GitHubError as err:
            errors.append(f"discovery '{label}': {err}")
    cache, new_cache, out, checks = state.get("discover_cache", {}), {}, {}, 0
    for key, (it, labels) in sorted(items.items(), key=lambda kv: kv[1][0].get("created_at", ""), reverse=True):
        repo = _repo_from(it)
        cand = Candidate.from_issue(repo, it, repo.split("/")[-1])
        hit = cache.get(key)
        if hit and hit.get("updated_at") == it.get("updated_at"):
            cand.bucket, cand.reason = hit["bucket"], hit.get("reason", "")
        elif checks < d.get("max_checks", 120):
            checks += 1
            try:
                classify(cand, gh.timeline(repo, cand.number), user=cfg["github_user"], now=now,
                         quiet_days=cfg.get("quiet_days", 45),
                         blocking_labels=cfg.get("blocking_labels") or DEFAULT_BLOCKING_LABELS)
            except GitHubError as err:
                errors.append(f"{key} timeline: {err}")
                continue
        else:
            continue                      # checked on a later run
        new_cache[key] = {"updated_at": it.get("updated_at"), "bucket": cand.bucket, "reason": cand.reason}
        if cand.bucket == "free":
            age = days_between(it["created_at"], now)
            cand.reason = " · ".join(labels) + f" · opened {age}d ago"
            cand.created_at = it.get("created_at", "")
            out[key] = cand
    return out, new_cache


def _triage_scopes(cfg):
    """(repo, labels, keywords) for each place to look for new issues.

    Your watched repos match by label, and also by keyword in the title or labels, so an issue
    filed without a component label still shows up. A watched repo with no labels matches every
    issue. `triage.repos` adds repos that are watched for new issues only."""
    t = cfg.get("triage") or {}
    words = list(t.get("keywords") or [])
    scopes = []
    for r in cfg["repos"]:
        labels = r.get("labels") or []
        scopes.append((r["repo"], labels, (words + list(r.get("keywords") or [])) if labels else []))
    for r in t.get("repos") or []:
        scopes.append((r["repo"], r.get("labels") or [], r.get("keywords") or []))
    return scopes


def _area(issue, repo, labels, words):
    """Where a new issue belongs: (tag, unlabelled), or None if it's outside your areas."""
    names = [lbl["name"] for lbl in issue.get("labels", [])]
    component = next((n for n in names if "/" in n), None)
    if not labels and not words:
        return component or repo.split("/")[-1], False
    for name in names:
        if name in labels:
            return name, False
    hay = " ".join([issue.get("title", "")] + names)
    if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}", hay, re.I) for w in words):
        return component or repo.split("/")[-1], component is None
    return None


def _human_repliers(comments, author):
    """Logins of people other than the author who commented, oldest first, bots left out."""
    out = []
    for c in comments:
        who = c.get("user") or {}
        login = who.get("login", "")
        if not login or login.lower() == author.lower() or who.get("type") == "Bot" or is_bot(login):
            continue
        out.append(login)
    return out


def _linked_prs(events):
    """Open PRs linked to an issue, from its timeline: [(ref, author)]. A PR linked by hand
    (a "connected" event) without a cross-reference shows up as ("a PR", "")."""
    found, connected = [], 0
    for ev in events:
        kind = ev.get("event")
        if kind == "connected":
            connected += 1
        elif kind == "disconnected":
            connected = max(0, connected - 1)
        elif kind == "cross-referenced":
            src = (ev.get("source") or {}).get("issue") or {}
            if "pull_request" not in src or src.get("state") != "open":
                continue
            if (src.get("pull_request") or {}).get("merged_at"):
                continue
            repo = _repo_from(src)
            ref = f"{repo}#{src.get('number')}" if repo else f"#{src.get('number')}"
            entry = (ref, (src.get("user") or {}).get("login", ""))
            if entry not in found:
                found.append(entry)
    if not found and connected:
        found.append(("a PR", ""))
    return found


def new_issues(gh, cfg, now, state, errors):
    """Every open issue opened in the last `triage.days` days in your areas, whatever its
    bucket, with who has replied. This is the triage queue: unlike "free", it also shows
    issues that were claimed, assigned or labelled as waiting the day they were opened.
    Comments are re-read only for issues that changed. Returns ({key: (item, no_reply,
    created_at)}, new_cache)."""
    t = cfg.get("triage") or {}
    if t.get("enabled") is False:
        return {}, {}
    since = (now - timedelta(days=t.get("days", 7))).strftime("%Y-%m-%d")
    user = cfg["github_user"].lower()
    cache, new_cache, out, checks = state.get("triage_cache", {}), {}, {}, 0
    for repo, labels, words in _triage_scopes(cfg):
        q = f"repo:{repo} is:issue is:open created:>={since}"
        try:
            hits = gh.search_issues(q, limit=t.get("max_per_repo", 300), sort="created")
        except GitHubError as err:
            errors.append(f"new issues in {repo}: {err}")
            continue
        for it in hits:
            key = f"{repo}#{it['number']}"
            author = (it.get("user") or {}).get("login", "")
            if "pull_request" in it or key in out or author.lower() == user:
                continue
            where = _area(it, repo, labels, words)
            if where is None:
                continue
            hit = cache.get(key)
            # Cache entries from before linked PRs were tracked have no "prs" and are re-read once.
            if hit and hit.get("updated_at") == it.get("updated_at") and "prs" in hit:
                repliers, prs = hit.get("repliers", []), [tuple(p) for p in hit["prs"]]
            elif checks < t.get("max_checks", 150):
                checks += 1
                try:
                    # One timeline read gives both the comments and any linked PRs.
                    events = gh.timeline(repo, it["number"])
                except GitHubError as err:
                    errors.append(f"{key} timeline: {err}")
                    continue
                comments = [ev for ev in events if ev.get("event") == "commented"]
                repliers = _human_repliers(comments, author)
                prs = _linked_prs(events)
            else:
                continue                      # checked on a later run
            new_cache[key] = {"updated_at": it.get("updated_at"), "repliers": repliers,
                              "prs": [list(p) for p in prs]}
            out[key] = _triage_item(key, it, where, repliers, user, now, prs)
    return out, new_cache


def _pr_note(prs, repo, user):
    """'PR #16168 open by souvik-biswas-dev', 'your PR #12 open', or 'a PR is linked'."""
    ref, author = prs[0]
    if ref == "a PR":
        return "a PR is linked"
    if ref.split("#")[0] == repo:
        ref = "#" + ref.split("#")[1]
    more = f" (+{len(prs) - 1} more)" if len(prs) > 1 else ""
    if author.lower() == user:
        return f"your PR {ref} open{more}"
    return f"PR {ref} open" + (f" by {author}" if author else "") + more


def _triage_item(key, it, where, repliers, user, now, prs=()):
    tag, unlabelled = where
    author = (it.get("user") or {}).get("login", "")
    names = {lbl["name"].lower() for lbl in it.get("labels", [])}
    if any(r.lower() == user for r in repliers):
        status = "you replied"
    elif not repliers:
        status = "no reply yet"
    else:
        n = len(repliers)
        status = f"{n} {'reply' if n == 1 else 'replies'}, last from {repliers[-1]}"
    bits = [status]
    if prs:
        bits.append(_pr_note(prs, key.split("#")[0], user))
    if "needs triage" in names:
        bits.append("needs triage")
    if unlabelled:
        bits.append("no component label")
    age = days_between(it.get("created_at") or it.get("updated_at") or iso(now), now)
    bits.append(f"by {author}, " + ("today" if age == 0 else f"{age}d ago"))
    # Tag the component when the title doesn't already name it; the repo is in the link anyway.
    named = "/" not in tag or tag.split("/")[-1].lower() in it.get("title", "").lower()
    prefix = "" if named else f"[{tag}] "
    item = Item(key, it.get("html_url", ""), it.get("title", ""), prefix + " · ".join(bits))
    # An issue someone already has a PR for doesn't need a triage reply from you.
    return item, status == "no reply yet" and not prs, it.get("created_at", "")


def _sorted_triage(found):
    """No reply yet first, then newest first."""
    rows = sorted(found.items(), key=lambda kv: (kv[1][2], kv[0]), reverse=True)
    return [v[0] for _, v in sorted(rows, key=lambda kv: not kv[1][1])]


def _when(value):
    try:
        return parse_time(value)
    except (TypeError, ValueError):
        return None


def _last_word(events, author, user, is_pr):
    """The last thing a person (not a bot) did on a thread: (login, time, kind)."""
    last = None

    def note(login, when, kind):
        nonlocal last
        t = _when(when)
        if login and t and not is_bot(login) and (last is None or t >= last[1]):
            last = (login.lower(), t, kind)

    for ev in events:
        kind = ev.get("event")
        if kind == "commented":
            note(actor_of(ev), ev.get("created_at"), "commented")
        elif kind == "reviewed":
            who = (ev.get("user") or {}).get("login", "")
            state = (ev.get("state") or "").lower()
            mine_approval = who.lower() == user and state == "approved" and author.lower() != user
            note(who, ev.get("submitted_at"), "approved" if mine_approval else "reviewed")
        elif kind == "line-commented":
            for c in ev.get("comments") or []:
                note((c.get("user") or {}).get("login", ""), c.get("created_at"), "commented")
        elif kind == "committed" and is_pr and author.lower() == user:
            note(author, (ev.get("committer") or {}).get("date"), "pushed")
        elif kind == "review_requested" and actor_of(ev).lower() == user:
            note(actor_of(ev), ev.get("created_at"), "re-requested")
    return last


WAIT_PHRASES = {
    "commented": "you commented {age}d ago",
    "reviewed": "you reviewed it {age}d ago",
    "opened": "you opened it {age}d ago",
    "pushed": "you pushed changes {age}d ago",
    "re-requested": "you asked for a re-review {age}d ago",
}


def waiting_on_others(gh, cfg, now, errors, skip=()):
    """Open threads where you had the last word `waiting_days`+ days ago and nobody has
    replied since: your comment, your issue, your pushed fixes or your re-review request.
    Approving someone else's PR isn't waiting. Returns {key: (age, Item)}."""
    days = cfg.get("waiting_days", 7)
    user = cfg["github_user"]
    lo = user.lower()
    since = (now - timedelta(days=cfg.get("followup_days", 60))).strftime("%Y-%m-%d")
    q = f"involves:{user} is:open updated:>={since} {_repo_scope(cfg)}"
    try:
        threads = gh.search_issues(q, limit=cfg.get("waiting_threads", 50))
    except GitHubError as err:
        errors.append(f"waiting-on-others search: {err}")
        return {}
    out = {}
    for th in threads:
        repo = _repo_from(th)
        key = f"{repo}#{th['number']}"
        is_pr = "pull_request" in th
        author = (th.get("user") or {}).get("login", "")
        if key in skip or (is_pr and th.get("draft")):
            continue
        try:
            events = gh.timeline(repo, th["number"])
        except GitHubError as err:
            errors.append(f"{key} timeline: {err}")
            continue
        last = _last_word(events, author, lo, is_pr)
        if last is None and author.lower() == lo and not is_pr and _when(th.get("created_at")):
            last = (lo, _when(th["created_at"]), "opened")
        if last is None or last[0] != lo or last[2] == "approved":
            continue
        age = max(0, (now - last[1]).days)
        if age < days:
            continue
        detail = WAIT_PHRASES[last[2]].format(age=age) + ", no reply since: a short nudge is fine"
        out[key] = (age, Item(key, th.get("html_url", ""), th.get("title", ""), detail))
    return out


def describe_events(events, user, since):
    """Short phrases for events by other humans after `since`, collapsed per person and kind."""
    phrases = []
    for ev in events:
        kind = ev.get("event")
        when = ev.get("submitted_at") or ev.get("created_at")
        if not when or parse_time(when) <= since:
            continue
        who = actor_of(ev)
        if not who or who.lower() == user.lower() or is_bot(who):
            continue
        if kind == "commented":
            phrase = f"{who} commented"
        elif kind == "reviewed":
            state = (ev.get("state") or "").lower()
            phrase = {"approved": f"{who} approved",
                      "changes_requested": f"{who} requested changes"}.get(state, f"{who} reviewed")
        elif kind == "merged":
            phrase = f"merged by {who}"
        elif kind == "closed":
            phrase = f"closed by {who}"
        elif kind == "reopened":
            phrase = f"reopened by {who}"
        elif kind == "assigned" and (ev.get("assignee") or {}).get("login", "").lower() == user.lower():
            phrase = f"{who} assigned you"
        else:
            continue
        if phrase not in phrases:
            phrases.append(phrase)
    if any(p.startswith("merged by") for p in phrases):
        phrases = [p for p in phrases if not p.startswith("closed by")]
    return phrases


def build_digest(gh, cfg, state, now, judge=None):
    """Returns (digest, new_state). On the first run it records a baseline instead of
    reporting everything as new."""
    digest = Digest()
    errors = digest.errors
    seen = state.get("seen", {})
    first_run = not state.get("last_run")

    prs = {}
    cands = scan_candidates(gh, cfg, now, errors, prs)
    by_bucket = {}
    for c in cands:
        by_bucket.setdefault(c.bucket, []).append(c)
    digest.counts = {b: len(v) for b, v in by_bucket.items()}

    free = {c.key: c for c in by_bucket.get("free", [])}
    quiet = {c.key: c for c in by_bucket.get("quiet", [])}

    try:
        requested = {f"{'/'.join(pr['repository_url'].split('/')[-2:])}#{pr['number']}": pr
                     for pr in review_requests(gh, cfg)}
    except GitHubError as err:
        errors.append(f"review requests: {err}")
        requested = None

    queue = unreviewed_prs(gh, cfg, now, prs, set(seen.get("review_queue", [])), errors)
    follow, my_claims = followups(gh, cfg, now, errors)
    my_waiting = waiting_prs(gh, cfg, now, errors)
    found, cache = discover(gh, cfg, now, state, {c.key for c in cands}, errors)
    aside, ai_cache = [], state.get("ai_cache", {})
    if judge is not None:
        # Your areas first, so a small call budget goes where it matters most.
        pool = list(free.values()) + [c for k, c in found.items() if k not in free]
        still, aside, ai_cache = ai.review_free(judge, gh, pool, now, state.get("ai_cache", {}), errors)
        keep = {c.key for c in still}
        free = {k: c for k, c in free.items() if k in keep}
        found = {k: c for k, c in found.items() if k in keep}
    triage_found, triage_cache = new_issues(gh, cfg, now, state, errors)
    waiting = waiting_on_others(gh, cfg, now, errors,
                                skip=set(my_waiting) | set(my_claims) | set(requested or {}))
    for key in list(follow):
        if key in (requested or {}):
            del follow[key]                    # already reported as a review request

    for key, pr in (requested or {}).items():
        if first_run or key not in seen.get("review_requested", []):
            digest.needs_you.append(Item(key, pr["html_url"], pr["title"],
                                         f"review requested by {pr['user']['login']}"))

    new_follow = [item for key, item in sorted(follow.items())
                  if first_run or key not in seen.get("followups", [])]
    for item in new_follow:
        item.detail = "PR opened on a thread you're in, " + item.detail
    digest.needs_you.extend(new_follow)
    for name, group in (("my_waiting", my_waiting), ("my_claims", my_claims)):
        digest.needs_you.extend(item for key, item in sorted(group.items())
                                if first_run or key not in seen.get(name, []))

    if first_run:
        digest.baseline = True
        top = sorted(free.values(), key=lambda c: c.updated_at, reverse=True)[:3]
        digest.to_pick = [_cand_item(c, "free") for c in top]
    else:
        since = parse_time(state["last_run"])
        digest.needs_you.extend(activity_on_mine(gh, cfg, since, errors))
        digest.to_pick = (
            [_cand_item(c, "free") for k, c in free.items() if k not in seen.get("free", [])]
            + [_cand_item(c, "quiet") for k, c in quiet.items() if k not in seen.get("quiet", [])]
        )
        digest.review_queue = [_queue_item(key, label, pr, now) for key, (repo, label, pr) in queue.items()
                               if key not in seen.get("review_queue", [])]
        digest.discovered = [_disc_item(c) for k, c in _newest(found) if k not in seen.get("discovered", [])]
        # A section added after the baseline starts quietly: the first run that has it only
        # records what's there, instead of announcing every current item as new.
        if "triage" in seen:
            digest.triage = _sorted_triage({k: v for k, v in triage_found.items() if k not in seen["triage"]})
        if "waiting" in seen:
            digest.needs_you.extend(item for key, (age, item) in sorted(waiting.items())
                                    if key not in seen["waiting"])

    # The full current lists, for the always-up-to-date board issue.
    by_recent = lambda items: sorted(items, key=lambda c: c.updated_at, reverse=True)  # noqa: E731
    digest.board_free = [_cand_item(c, "free") for c in by_recent(free.values())]
    digest.board_aside = [Item(c.key, c.url, c.title,
                               ("someone has it" if verdict == "taken" else "not ready to start") + f": {reason}")
                          for c, verdict, reason in sorted(aside, key=lambda a: a[0].updated_at, reverse=True)]
    digest.board_quiet = [_cand_item(c, "quiet") for c in by_recent(quiet.values())]
    digest.board_queue = sorted((_queue_item(key, label, pr, now) for key, (repo, label, pr) in queue.items()),
                                key=lambda i: i.key)
    digest.board_followups = [Item(i.key, i.url, i.title, i.detail.replace("PR opened on a thread you're in, ", ""))
                              for _, i in sorted(follow.items())]
    digest.board_my_waiting = [i for _, i in sorted(my_waiting.items())]
    digest.board_my_claims = [i for _, i in sorted(my_claims.items())]
    limit = (cfg.get("discover") or {}).get("limit", 30)
    digest.board_discovered = [_disc_item(c) for _, c in _newest(found)][:limit]
    if (cfg.get("discover") or {}).get("orgs"):
        digest.counts["discovered"] = len(found)
    digest.board_triage = _sorted_triage(triage_found)
    digest.board_waiting = [item for _, (age, item) in sorted(waiting.items(), key=lambda kv: (-kv[1][0], kv[0]))]
    if triage_found:
        digest.counts["new_issues"] = len(triage_found)
        digest.counts["no_reply"] = sum(1 for v in triage_found.values() if v[1])

    def merged(name, current):
        # After a partial scan, keep old entries so they don't re-alert tomorrow.
        old = set(seen.get(name, []))
        return sorted(old | set(current)) if errors else sorted(current)

    new_state = {
        "last_run": iso(now),
        "seen": {
            "free": merged("free", free),
            "quiet": merged("quiet", quiet),
            "review_requested": (sorted(requested) if requested is not None
                                 else seen.get("review_requested", [])),
            "review_queue": merged("review_queue", queue),
            "followups": merged("followups", follow),
            "my_waiting": merged("my_waiting", my_waiting),
            "my_claims": merged("my_claims", my_claims),
            "discovered": merged("discovered", found),
            "triage": merged("triage", triage_found),
            "waiting": merged("waiting", waiting),
        },
        "discover_cache": cache,
        "ai_cache": ai_cache,
        "triage_cache": triage_cache,
    }
    return digest, new_state


def _newest(found):
    return sorted(found.items(), key=lambda kv: (getattr(kv[1], "created_at", ""), kv[0]), reverse=True)


def _disc_item(c):
    return Item(c.key, c.url, c.title, f"[{c.repo.split('/')[-1]}] {c.reason}")


def _queue_item(key, label, pr, now):
    age = days_between(pr["created_at"], now)
    flags = [lbl["name"] for lbl in pr.get("labels", []) if lbl["name"] == "first-time contributor"]
    detail = ", ".join([f"by {pr['user']['login']}", f"{age}d with no review"] + flags)
    tag = "" if label.split("/")[-1].lower() in pr["title"].lower() else f"[{label}] "
    return Item(key, pr["html_url"], pr["title"], tag + detail)


def _cand_item(c, bucket):
    # Skip the "[receiver/k8scluster]" tag when the title already names the component.
    short = c.component.split("/")[-1].lower()
    tag = "" if short in c.title.lower() else f"[{c.component}] "
    detail = tag + ("free" if bucket == "free" else c.reason)
    if bucket == "free" and c.reason:
        detail += f"; {c.reason}"
    return Item(c.key, c.url, c.title, detail)


def full_report(cands):
    """Markdown listing of every bucket, like the old otel-scout.sh output."""
    order = [("free", "Free"), ("quiet", "Claimed but quiet"), ("blocked", "Blocked"),
             ("mine", "Yours"), ("taken", "Taken")]
    lines = []
    for bucket, title in order:
        group = sorted((c for c in cands if c.bucket == bucket), key=lambda c: c.updated_at, reverse=True)
        lines.append(f"## {title} ({len(group)})\n")
        for c in group:
            reason = f" — {c.reason}" if c.reason else ""
            lines.append(f"- [{c.key}]({c.url}) [{c.component}] {c.title}{reason}")
        lines.append("")
    return "\n".join(lines)
