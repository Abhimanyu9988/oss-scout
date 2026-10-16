"""GitHub delivery: one 'Contribution board' issue in your own scout repo.

The issue body is the live board (what's waiting on you or on others, new issues to triage,
everything free, claimed-but-quiet and waiting for a first review), rewritten on every run. When something changed, a comment mentioning
you carries the day's news, so it reaches the GitHub app and your email.

Two things keep the board polite if the repo is public:
- links go through redirect.github.com, which (as Dependabot does) avoids leaving a
  "mentioned this issue" note on every upstream issue;
- '@' in titles is neutralised so the board never pings anyone but you;
- issue references in text (owner/repo#123, #123) are broken up, since GitHub would link them
  and leave the same note.
"""

import re

LABEL = "oss-scout"
LABEL_COLOR = "0e8a16"
TITLE = "Contribution board"
MAX_COMMENT_LINES = 25
SECTION_LIMIT = 25          # items per board section; the rest are counted, not listed
MAX_COMMENTS = 100          # then the board moves to a fresh issue, so the page stays quick
BODY_LIMIT = 60000          # GitHub refuses issue bodies over 65,536 characters


def quiet_link(url):
    return url.replace("https://github.com/", "https://redirect.github.com/", 1)


# owner/repo#123, owner#123 or a bare #123. GitHub turns these into links, and a link from an issue
# or comment puts a "mentioned this" event on the issue it points to, so in the board they're
# broken with a zero-width space. A bare #123 would also point at this repo, not the upstream one.
REF_RE = re.compile(r"(?<![\w&])((?:[\w.-]+(?:/[\w.-]+)?)?#)(\d+)")
ZWSP = "​"


def safe(text):
    """Text for the board and its comments that pings nobody and links nowhere upstream."""
    text = text.replace("https://github.com/", "https://redirect.github.com/")
    text = REF_RE.sub(lambda m: m.group(1) + ZWSP + m.group(2), text)
    return text.replace("@", "@" + ZWSP).replace("\n", " ")


def _ref(key):
    repo, number = key.rsplit("#", 1)
    return f"{repo.split('/')[-1]} {number}"


def _line(item):
    detail = f" — {safe(item.detail)}" if item.detail else ""
    return f"- [{_ref(item.key)}]({quiet_link(item.url)}) {safe(item.title)}{detail}"


def _capped(heading, items, limit=None, empty="Nothing right now.", more="see the board above"):
    lines = [f"### {heading} ({len(items)})", ""]
    if not items:
        lines.append(f"_{empty}_")
    shown = items if limit is None else items[:limit]
    lines += [_line(i) for i in shown]
    if limit is not None and len(items) > limit:
        lines.append(f"- _…and {len(items) - limit} more, {more}_")
    lines.append("")
    return lines


def _collapsed(heading, items, limit, empty="Nothing right now."):
    """A section folded into a <details> block: only its title and count show until opened."""
    lines = ["<details>", f"<summary><b>{heading} ({len(items)})</b></summary>", ""]
    if not items:
        lines.append(f"_{empty}_")
    lines += [_line(i) for i in items[:limit]]
    if len(items) > limit:
        lines.append(f"- _…and {len(items) - limit} more, not listed here_")
    return lines + ["", "</details>", ""]


def summary_line(digest):
    """One line that says at a glance whether there's anything to open."""
    needs = (len(digest.board_followups) + len(digest.board_my_waiting) + len(digest.board_my_claims)
             + len(digest.board_waiting))
    bits = [f"**{needs} need{'s' if needs == 1 else ''} you**"]
    if digest.board_activity:
        bits.append(f"{len(digest.board_activity)} active thread{'s' if len(digest.board_activity) != 1 else ''}")
    bits.append(f"{len(digest.board_mine)} open PR{'s' if len(digest.board_mine) != 1 else ''} of yours")
    if digest.board_triage:
        no_reply = digest.counts.get("no_reply", 0)
        bits.append(f"{len(digest.board_triage)} to triage ({no_reply} with no reply)")
    bits.append(f"{len(digest.board_free)} free in your areas")
    if digest.board_discovered:
        bits.append(f"{len(digest.board_discovered)} free across your organisations")
    bits.append(f"{len(digest.board_quiet)} claimed but quiet")
    bits.append(f"{len(digest.board_queue)} to review")
    if digest.board_aside:
        bits.append(f"{len(digest.board_aside)} set aside by AI")
    if digest.hidden:
        bits.append(f"{len(digest.hidden)} hidden")
    return " · ".join(bits)


def render_board(digest, cfg, now, home_repo=None):
    cap = cfg.get("board_section_limit", SECTION_LIMIT)

    def _section(heading, items, empty="Nothing right now."):
        return _capped(heading, items, cap, empty=empty, more="not listed here")

    def _folded(heading, items, empty="Nothing right now."):
        return _collapsed(heading, items, cap, empty=empty)

    tool = f"[oss-scout](https://github.com/{home_repo})" if home_repo else "oss-scout"
    repos = ", ".join(r["repo"] for r in cfg["repos"])
    areas = ", ".join(sorted({lbl for r in cfg["repos"] for lbl in (r.get("labels") or ["everything"])}))
    d = cfg.get("discover") or {}
    lines = [f"Updated {now.strftime('%a %d %b %Y, %H:%M')} UTC · watching {safe(repos)}", "",
             f"Areas: {safe(areas)}", ""]
    if d.get("orgs"):
        labels = " or ".join(f"`{x}`" for x in (d.get("labels") or ["good first issue", "help wanted"]))
        lines += [f"Also looking across {safe(', '.join(d['orgs']))} for issues labelled {labels}.", ""]
    t = cfg.get("triage") or {}
    extra = [r["repo"] for r in t.get("repos") or []]
    if t.get("enabled") is not False and (extra or t.get("keywords")):
        bits = []
        if extra:
            bits.append(f"new issues in {safe(', '.join(extra))}")
        if t.get("keywords"):
            bits.append("unlabelled ones mentioning " + safe(", ".join(t["keywords"])))
        lines += ["Triage also covers " + " and ".join(bits) + ".", ""]
    lines += [summary_line(digest), "", "## Needs you", ""]
    days = cfg.get("activity_days", 7)
    lines += _section(f"Activity on your threads, last {days} days", digest.board_activity,
                      empty=f"Nobody else has posted on your threads in the last {days} days.")
    lines += _section("Your open PRs", digest.board_mine, empty="You have no open PRs here.")
    lines += _section("PRs to review on threads you're in", digest.board_followups,
                      empty="Nothing waiting for you.")
    lines += _section(f"Your PRs with no review after {cfg.get('ping_after_days', 7)}+ days", digest.board_my_waiting,
                      empty="None. The CNCF contributor FAQ suggests a polite ping after about a week.")
    lines += _section(f"Issues you claimed {cfg.get('claim_reminder_days', 14)}+ days ago with no PR",
                      digest.board_my_claims, empty="None.")
    lines += _section(f"Waiting on others: you spoke last {cfg.get('waiting_days', 7)}+ days ago",
                      digest.board_waiting, empty="Nobody owes you a reply.")
    t = cfg.get("triage") or {}
    if t.get("enabled") is not False:
        days = t.get("days", 7)
        no_reply = digest.counts.get("no_reply", 0)
        lines += ["## To triage", ""]
        lines += _folded(f"Opened in the last {days} days in your areas, {no_reply} with no reply yet",
                         digest.board_triage, empty=f"No new issues in the last {days} days.")
    lines += ["## To pick up", ""]
    lines += _folded("Free in your areas", digest.board_free,
                     empty="No unclaimed issues in these areas today.")
    if d.get("orgs"):
        lines += _folded(f"Free across {', '.join(d['orgs'])}", digest.board_discovered,
                         empty="Nothing new and unclaimed right now.")
    if digest.board_aside:
        lines += _folded("Free by the rules, but the AI check says not to start", digest.board_aside)
    lines += _folded(f"Claimed by someone else but quiet for {cfg.get('quiet_days', 45)}+ days", digest.board_quiet)
    lines += ["## To review", ""]
    lines += _folded(f"PRs in your areas waiting {cfg.get('stale_review_days', 7)}+ days for a first review",
                     digest.board_queue)
    if digest.hidden:
        lines += ["<details>", f"<summary><b>Hidden ({len(digest.hidden)})</b></summary>", ""]
        for key in digest.hidden:
            repo, number = key.rsplit("#", 1)
            lines.append(f"- [{_ref(key)}]({quiet_link(f'https://github.com/{repo}/issues/{number}')})")
        lines += ["", "</details>", ""]
    lines += [
        "---",
        "_To hide an item from this board, reply `/hide repo#123` (or `/unhide repo#123`, or paste the "
        "issue link); the next run applies it and reacts with a thumbs-up._",
        "",
        "_Free means no linked PR, no claim comment and no blocking label. Claim detection is a "
        "heuristic, so read the whole thread and ask a maintainer before starting. Generated by "
        f"{tool}, which only reads public data._",
    ]
    if digest.errors:
        lines += ["", f"_Last run was partial: {len(digest.errors)} request(s) failed._"]
    body = "\n".join(lines) + "\n"
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT].rsplit("\n", 1)[0] + "\n\n_Board cut short to fit GitHub's size limit. " \
               "Lower `board_section_limit` or watch fewer labels._\n"
    return body


def render_comment(digest, cfg, now):
    user = cfg["github_user"]
    lines = [f"@{user} · {now.strftime('%a %d %b')}", ""]
    if digest.baseline:
        c = digest.counts
        lines += [
            "**Watching from today.** From now on you'll get a comment here only when something changes.",
            "",
            f"{c.get('free', 0)} free · {c.get('quiet', 0)} claimed but quiet · "
            f"{c.get('blocked', 0)} blocked · {c.get('taken', 0)} taken"
            + (f" · {c['discovered']} free across your organisations" if "discovered" in c else "")
            + (f" · {c['new_issues']} opened recently in your areas" if c.get("new_issues") else "")
            + ". The full lists are in the issue above.",
            "",
        ]
    if digest.needs_you:
        lines += _capped("Needs you", digest.needs_you, MAX_COMMENT_LINES)
    if digest.triage:
        lines += _capped("Newly opened in your areas", digest.triage, MAX_COMMENT_LINES)
    if digest.to_pick:
        heading = "Most recent free issues" if digest.baseline else "New to pick up"
        lines += _capped(heading, digest.to_pick, MAX_COMMENT_LINES)
    if digest.discovered:
        lines += _capped("New across your organisations", digest.discovered, 5)
    if digest.review_queue:
        lines += _capped("Newly waiting for a first review", digest.review_queue, MAX_COMMENT_LINES)
    if digest.errors:
        lines += [f"_Partial scan: {len(digest.errors)} request(s) failed. First: {safe(digest.errors[0][:200])}_"]
    return "\n".join(lines).rstrip() + "\n"


def publish(writer, repo, digest, cfg, now, comment=True):
    """Create or update the board issue; add a comment when there's news.
    Returns the issue's URL."""
    body = render_board(digest, cfg, now, repo)
    issue = writer.find_open_issue(repo, LABEL)
    if issue is not None and issue.get("comments", 0) >= cfg.get("board_max_comments", MAX_COMMENTS):
        old = issue
        writer.ensure_label(repo, LABEL, LABEL_COLOR, "Daily contribution board from oss-scout")
        issue = writer.create_issue(repo, TITLE, body, [LABEL])
        writer.add_comment(repo, old["number"], f"Continued in #{issue['number']}, to keep this page quick to load.")
        writer.close_issue(repo, old["number"])
    elif issue is None:
        writer.ensure_label(repo, LABEL, LABEL_COLOR, "Daily contribution board from oss-scout")
        issue = writer.create_issue(repo, TITLE, body, [LABEL])
    else:
        writer.update_issue_body(repo, issue["number"], body)
    if comment:
        writer.add_comment(repo, issue["number"], render_comment(digest, cfg, now))
    return issue["html_url"]
