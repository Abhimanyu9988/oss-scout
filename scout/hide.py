"""Hide items from the board by replying to the board issue.

    /hide semantic-conventions#3119
    /unhide semantic-conventions#3119

Each line takes one or more references: `repo#123`, `owner/repo#123` or an issue/PR link.
Only your own comments count. Each run applies the comments posted since the last run, in
order, and reacts 👍 to each one (😕 when a reference couldn't be matched). Hidden items
drop out of every section and today's comment, and are listed at the end of the board so
nothing disappears for good.
"""

import re

from .github import GitHubError

CMD_RE = re.compile(r"^[ \t]*/(hide|unhide)[ \t]+(.+?)[ \t]*$", re.I | re.M)
REF_RE = re.compile(
    r"https?://(?:redirect\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)"
    r"|(?:([\w.-]+)/)?([\w.-]+)#(\d+)")

DIGEST_LISTS = ("needs_you", "to_pick", "review_queue", "discovered", "triage", "board_free", "board_quiet",
                "board_queue", "board_followups", "board_my_waiting", "board_my_claims", "board_discovered",
                "board_triage", "board_waiting", "board_aside")


def base_key(key):
    """'owner/repo#12:linked' -> 'owner/repo#12'."""
    return key.split(":", 1)[0]


def parse_refs(text):
    """[(owner or None, repo, number)] for every reference in `text`."""
    refs = []
    for m in REF_RE.finditer(text):
        if m.group(1):
            refs.append((m.group(1), m.group(2), int(m.group(3))))
        else:
            refs.append((m.group(4), m.group(5), int(m.group(6))))
    return refs


def read_commands(comments, user, after_id=0):
    """[(comment_id, [(action, refs)])] from `user`'s comments newer than `after_id`, oldest first."""
    out = []
    for c in sorted(comments, key=lambda c: c.get("id", 0)):
        if c.get("id", 0) <= after_id:
            continue
        if ((c.get("user") or {}).get("login") or "").lower() != user.lower():
            continue
        cmds = [(m.group(1).lower(), parse_refs(m.group(2))) for m in CMD_RE.finditer(c.get("body") or "")]
        if cmds:
            out.append((c["id"], cmds))
    return out


def resolve(ref, known, repos):
    """Full 'owner/repo#N' for a reference, or None if the short repo name is unknown or ambiguous.
    `known` is every key on the board today; `repos` is every configured 'owner/repo'."""
    owner, repo, number = ref
    if owner:
        return f"{owner}/{repo}#{number}"
    suffix = f"/{repo}#{number}".lower()
    hits = {k for k in known if k.lower().endswith(suffix)}
    if not hits:
        hits = {f"{r}#{number}" for r in repos if r.lower().endswith(f"/{repo}".lower())}
    return hits.pop() if len(hits) == 1 else None


def all_keys(digest):
    return {base_key(i.key) for name in DIGEST_LISTS for i in getattr(digest, name)}


def configured_repos(cfg):
    repos = [r["repo"] for r in cfg.get("repos") or []]
    repos += [r["repo"] for r in (cfg.get("triage") or {}).get("repos") or []]
    return repos


def apply(hidden, commands, known, repos):
    """Replays `commands` onto the `hidden` set. Returns (hidden, reactions) where reactions
    is [(comment_id, '+1' or 'confused')]."""
    hidden = {k.lower(): k for k in hidden}
    reactions = []
    for comment_id, cmds in commands:
        ok = True
        for action, refs in cmds:
            if not refs:
                ok = False
            for ref in refs:
                key = resolve(ref, known, repos)
                if key is None:
                    ok = False
                elif action == "hide":
                    hidden[key.lower()] = key
                else:
                    hidden.pop(key.lower(), None)
        reactions.append((comment_id, "+1" if ok else "confused"))
    return sorted(hidden.values(), key=str.lower), reactions


def filter_digest(digest, hidden):
    lower = {k.lower() for k in hidden}
    for name in DIGEST_LISTS:
        setattr(digest, name, [i for i in getattr(digest, name) if base_key(i.key).lower() not in lower])
    digest.hidden = list(hidden)


def process(gh, cfg, state, new_state, digest, board_repo, advance=True):
    """Reads new /hide and /unhide comments on the board issue, updates the hidden list in
    `new_state` and removes hidden items from `digest`. Returns the reactions to post.
    With advance=False (a dry run) the comments are applied but left to be read again, so the
    next real run still reacts to them."""
    hidden = list(state.get("hidden", []))
    cursor = state.get("hide_cursor", 0)
    commands = []
    issue = None
    if board_repo and hasattr(gh, "find_open_issue"):
        try:
            from .board import LABEL
            issue = gh.find_open_issue(board_repo, LABEL)
            if issue and issue.get("comments", 0):
                commands = read_commands(gh.issue_comments(board_repo, issue["number"]), cfg["github_user"], cursor)
        except GitHubError as err:
            digest.errors.append(f"reading /hide comments: {err}")
    hidden, reactions = apply(hidden, commands, all_keys(digest), configured_repos(cfg))
    filter_digest(digest, hidden)
    new_state["hidden"] = hidden
    new_state["hide_cursor"] = max([cursor] + [cid for cid, _ in commands]) if advance else cursor
    return reactions


def post_reactions(writer, board_repo, reactions):
    """Best effort: a failed reaction is reported, never fatal."""
    failed = []
    for comment_id, content in reactions:
        try:
            writer.add_reaction(board_repo, comment_id, content)
        except GitHubError as err:
            failed.append(f"reaction on comment {comment_id}: {err}")
    return failed
