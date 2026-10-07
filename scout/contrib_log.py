"""A public log of your open-source contributions, rebuilt from GitHub's own record.

Collects, for the orgs and repos you configure:
  - PRs you opened (with merge state) and issues you opened
  - reviews you left on other people's PRs, each linking to the review itself
  - comments you wrote, each linking to the comment itself

and writes them to a JSON data file plus a marked section of a README (typically your
profile README). Output is deterministic, so files only change when your record does.

Daily runs look back `lookback_days`; a full rebuild (--full, or when there's no data
file yet) re-reads your whole history and also drops anything you've since deleted.
"""

import json
import os
import re
from datetime import date, timedelta

from . import scholar
from .github import GitHubError

SEARCH_CAP = 1000
START = "<!-- contrib-log:start -->"
END = "<!-- contrib-log:end -->"
DEFAULT_COMPONENT_PATTERNS = [
    r"^(receiver|processor|exporter|extension|connector|internal|pkg|cmd|confmap|testbed)/",
    r"^area:", r"^chart:", r"^comp:", r"^component:",
]
DEFAULTS = {
    "orgs": [],
    "repos": [],
    "readme_path": "README.md",
    "data_path": "contributions.json",
    "annotations_path": "annotations.yaml",
    "lookback_days": 14,
    "readme_title": "Open-source contributions",
    "readme_include_own_comments": False,
    "readme_show_tags": False,
    "readme_hide_closed_prs": True,
    "readme_recent_items": 5,
    "orcid": "",
    "openalex_email": "",
    "links": {},
    "tool_url": "https://github.com/Abhimanyu9988/oss-scout",
}


def load_log_config(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg.get("github_user"):
        raise ValueError(f"{path}: github_user is required")
    merged = dict(DEFAULTS)
    merged.update(cfg)
    if not (merged["orgs"] or merged["repos"]):
        raise ValueError(f"{path}: list at least one org or repo")
    merged.setdefault("component_label_patterns", DEFAULT_COMPONENT_PATTERNS)
    return merged


# ---------------------------------------------------------------- collection

def repo_of(item):
    return "/".join(item["repository_url"].rstrip("/").split("/")[-2:])


def component_of(labels, patterns):
    for pattern in patterns:
        for label in labels:
            if re.search(pattern, label):
                return label
    return "general"


def _day(value):
    return value[:10] if value else ""


def search_all(gh, query, field, start, end, errors):
    """Every result of `query` with `field` in start..end (dates). Splits the range in
    half whenever a slice reaches GitHub's 1,000-result search cap."""
    q = f"{query} {field}:{start.isoformat()}..{end.isoformat()}"
    total, items = gh.search_total(q, 1)
    if total >= SEARCH_CAP and start < end:
        mid = start + timedelta(days=(end - start).days // 2)
        return (search_all(gh, query, field, start, mid, errors)
                + search_all(gh, query, field, mid + timedelta(days=1), end, errors))
    if total >= SEARCH_CAP:
        errors.append(f"more than {SEARCH_CAP} results on {start}; some are missing: {q}")
    results, page = list(items), 2
    while items and len(results) < min(total, SEARCH_CAP):
        _, items = gh.search_total(q, page)
        results.extend(items)
        page += 1
    return results


def _scopes(cfg):
    return [f"org:{o}" for o in cfg["orgs"]] + [f"repo:{r}" for r in cfg["repos"]]


def _base(item, patterns):
    return {
        "repo": repo_of(item),
        "number": item["number"],
        "title": item.get("title", ""),
        "component": component_of([lbl["name"] for lbl in item.get("labels", [])], patterns),
    }


DETAIL_KEYS = ("additions", "deletions", "files", "discussion", "merged_by", "approved_by",
               "fixes", "recognition")
FIXES_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s+"
    r"((?:[\w.-]+/[\w.-]+)?#\d+|https://github\.com/[\w.-]+/[\w.-]+/issues/\d+)", re.I)
PRAISE_RE = re.compile(
    r"\b(great|nice|awesome|excellent|appreciate[ds]?|good catch|well done|helpful|kudos|brilliant|"
    r"fantastic|valuable|impressive|love (?:this|it)|thanks? (?:a lot|so much)|thank you so much|"
    r"thanks? (?:you )?for (?:picking|taking|tackling|fixing|working|digging|investigating|"
    r"addressing|the (?:fix|thorough|careful|detailed|quick)|this (?:fix|work|contribution)))", re.I)
NOT_PRAISE_RE = re.compile(r"@[\w-]+/[\w-]+|please (?:take a look|review)|could someone|ptal", re.I)


def _is_bot(login):
    return bool(re.search(r"\[bot\]$|bot$|^github-actions|dashboard", login or "", re.I))


def _clean(body):
    text = re.sub(r"```.*?```", " ", body or "", flags=re.S)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith((">", "#", "|", "- [", "* ["))]
    text = " ".join(lines)
    text = re.sub(r"[*_`]+", "", text)
    return " ".join(text.split())


def praise(body, limit=240):
    """The sentence(s) of a comment that praise the work, or '' if there's no real praise."""
    if not body or NOT_PRAISE_RE.search(body):
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", _clean(body))
    hits = [s for s in sentences if PRAISE_RE.search(s)]
    if not hits:
        return ""
    i = sentences.index(hits[0])
    text = hits[0]
    if len(text) < 60 and i + 1 < len(sentences):      # a short "Great work!" reads better with its follow-up
        text = f"{text} {sentences[i + 1]}"
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def _excerpt(body, limit=200):
    text = _clean(body)
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def _fix_url(ref, repo):
    if ref.startswith("https://"):
        return ref
    target, number = ref.split("#")
    return f"https://github.com/{target or repo}/issues/{number}"


def merged_pr_details(gh, repo, number, user):
    """Size, discussion, approvals, merger, fixed issues and words of recognition for a merged PR."""
    pr = gh.pull(repo, number)
    reviews = gh.all_pr_reviews(repo, number)
    comments = gh.issue_comments(repo, number)
    approved = sorted({(r.get("user") or {}).get("login", "") for r in reviews
                       if r.get("state") == "APPROVED" and (r.get("user") or {}).get("login", "").lower() != user.lower()})
    fixes = sorted({_fix_url(m.group(1), repo) for m in FIXES_RE.finditer(pr.get("body") or "")})
    recognition = []
    for c in list(comments) + [r for r in reviews if r.get("body")]:
        who = (c.get("user") or {}).get("login", "")
        if not who or who.lower() == user.lower() or _is_bot(who):
            continue
        quote = praise(c.get("body"))
        if quote:
            recognition.append({"by": who, "text": quote, "url": c.get("html_url", ""),
                                "date": _day(c.get("created_at") or c.get("submitted_at"))})
    return {
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "files": pr.get("changed_files", 0),
        "discussion": pr.get("comments", 0) + pr.get("review_comments", 0),
        "merged_by": (pr.get("merged_by") or {}).get("login", ""),
        "approved_by": [a for a in approved if a],
        "fixes": fixes,
        "recognition": sorted(recognition, key=lambda r: (r["date"], r["url"])),
    }


def collect(gh, cfg, existing, today, full=False):
    """Returns (records, errors). `existing` is the previous records list."""
    user = cfg["github_user"]
    patterns = cfg["component_label_patterns"]
    errors = []
    if full or not existing:
        created = gh.user(user).get("created_at") or "2008-01-01T00:00:00Z"
        field, start = "created", date.fromisoformat(created[:10])
        records = {}
    else:
        field, start = "updated", today - timedelta(days=cfg["lookback_days"])
        records = {r["url"]: r for r in existing}

    def replace_children(parent_url, kind, new):
        for url in [u for u, r in records.items() if r.get("parent_url") == parent_url and r["type"] == kind]:
            del records[url]
        for rec in new:
            records[rec["url"]] = rec

    for scope in _scopes(cfg):
        # PRs and issues you opened
        try:
            for item in search_all(gh, f"author:{user} {scope}", field, start, today, errors):
                rec = _base(item, patterns)
                rec["url"] = item["html_url"]
                rec["date"] = _day(item["created_at"])
                if "pull_request" in item:
                    merged_at = (item.get("pull_request") or {}).get("merged_at")
                    rec["type"] = "pr"
                    rec["state"] = "merged" if merged_at else item["state"]
                    if merged_at:
                        rec["merged"] = _day(merged_at)
                        try:
                            rec.update(merged_pr_details(gh, repo_of(item), item["number"], user))
                        except GitHubError as err:
                            errors.append(f"details for {item['html_url']}: {err}")
                            old = records.get(rec["url"], {})
                            rec.update({k: old[k] for k in DETAIL_KEYS if k in old})
                else:
                    rec["type"] = "issue"
                    rec["state"] = item["state"]
                records[rec["url"]] = rec
        except GitHubError as err:
            errors.append(f"authored items in {scope}: {err}")

        # Reviews you left on other people's PRs
        try:
            prs = search_all(gh, f"is:pr reviewed-by:{user} -author:{user} {scope}", field, start, today, errors)
        except GitHubError as err:
            errors.append(f"reviewed PRs in {scope}: {err}")
            prs = []
        for pr in prs:
            try:
                reviews = gh.all_pr_reviews(repo_of(pr), pr["number"])
            except GitHubError as err:
                errors.append(f"reviews on {pr['html_url']}: {err}")
                continue
            mine = []
            for rv in reviews:
                if (rv.get("user") or {}).get("login", "").lower() != user.lower() or rv.get("state") == "PENDING":
                    continue
                rec = _base(pr, patterns)
                rec.update(type="review", url=rv["html_url"], parent_url=pr["html_url"],
                           date=_day(rv.get("submitted_at")), review_state=rv["state"].lower(),
                           author=(pr.get("user") or {}).get("login", ""))
                mine.append(rec)
            replace_children(pr["html_url"], "review", mine)

        # Comments you wrote (search only finds the thread; the comment links come from its comments)
        try:
            threads = search_all(gh, f"commenter:{user} {scope}", field, start, today, errors)
        except GitHubError as err:
            errors.append(f"comment threads in {scope}: {err}")
            threads = []
        for thread in threads:
            try:
                comments = gh.issue_comments(repo_of(thread), thread["number"])
            except GitHubError as err:
                errors.append(f"comments on {thread['html_url']}: {err}")
                continue
            own_thread = (thread.get("user") or {}).get("login", "").lower() == user.lower()
            mine = []
            for c in comments:
                if (c.get("user") or {}).get("login", "").lower() != user.lower():
                    continue
                rec = _base(thread, patterns)
                rec.update(type="comment", url=c["html_url"], parent_url=thread["html_url"],
                           date=_day(c["created_at"]), on_own_item=own_thread,
                           on="pr" if "pull_request" in thread else "issue")
                mine.append(rec)
            replace_children(thread["html_url"], "comment", mine)

    return sort_records(records.values()), errors


def sort_records(records):
    return sorted(records, key=lambda r: (r.get("date", ""), r["url"]), reverse=True)


# ---------------------------------------------------------------- annotations

def load_notes_file(path):
    """Raw mapping from a YAML or JSON notes file; {} if missing or only comments."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not re.search(r"^\s*[^#\s]", text, re.M):
        return {}
    if path.endswith(".json"):
        return json.loads(text) or {}
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        raise SystemExit(f"{path} needs PyYAML: pip install pyyaml (or use a .json file)") from None
    return yaml.safe_load(text) or {}


def parse_url_notes(data):
    """{url: {"note", "tags"}} from the URL-keyed entries of a notes mapping."""
    out = {}
    for key, value in data.items():
        if not str(key).startswith("http"):
            continue
        if isinstance(value, str):
            value = {"note": value}
        value = value or {}
        out[str(key).strip()] = {"note": str(value.get("note", "") or "").strip(),
                                 "tags": [str(t) for t in value.get("tags", []) or []]}
    return out


def parse_extras(data):
    """Work outside GitHub (talks, meetings, program committees, posts) from `extras:`."""
    out = []
    for i, e in enumerate(data.get("extras", []) or []):
        if not isinstance(e, dict) or not e.get("title"):
            continue
        when = str(e.get("date", ""))[:10]
        url = str(e.get("url", "") or "").strip()
        rec = {"type": "extra", "kind": str(e.get("type", "other")).strip().lower() or "other",
               "title": str(e["title"]).strip(), "date": when, "repo": "", "number": 0,
               "component": "general",
               "url": url or f"extra:{when}:{re.sub(r'[^a-z0-9]+', '-', str(e['title']).lower()).strip('-')}"}
        if url:
            rec["link"] = url
        if e.get("note"):
            rec["note"] = str(e["note"]).strip()
        if e.get("tags"):
            rec["tags"] = [str(t) for t in e["tags"]]
        out.append(rec)
    return out


def load_annotations(path):
    """URL notes only (kept for callers that don't need extras)."""
    return parse_url_notes(load_notes_file(path))


def apply_annotations(records, annotations):
    """Annotations match a record by its exact URL (a PR, an issue, a review or a comment)."""
    out = []
    for rec in records:
        if rec["type"] == "extra":
            out.append(rec)
            continue
        rec = {k: v for k, v in rec.items() if k not in ("note", "tags")}
        ann = annotations.get(rec["url"])
        if ann:
            if ann.get("note"):
                rec["note"] = ann["note"]
            if ann.get("tags"):
                rec["tags"] = ann["tags"]
        out.append(rec)
    return out


# ---------------------------------------------------------------- rendering

TYPE_LABELS = {
    ("pr", "merged"): "PR · merged", ("pr", "open"): "PR · open", ("pr", "closed"): "PR · closed",
    ("issue", "open"): "Issue · open", ("issue", "closed"): "Issue · closed",
}
REVIEW_LABELS = {"approved": "Review · approved", "changes_requested": "Review · changes requested",
                 "commented": "Review · comments", "dismissed": "Review · dismissed"}
EXTRA_LABELS = {"talk": "Talk", "meeting": "Meeting", "review": "Program committee", "post": "Writing",
                "mentoring": "Mentoring", "workshop": "Workshop", "podcast": "Podcast", "other": "Other"}


REVIEW_RANK = {"approved": 0, "changes_requested": 1, "commented": 2, "dismissed": 3}


def collapse_reviews(records):
    """One entry per reviewed PR: the strongest outcome, the latest date, and how many rounds."""
    out, groups = [], {}
    for r in records:
        if r["type"] == "review":
            groups.setdefault(r.get("parent_url") or r["url"], []).append(r)
        else:
            out.append(r)
    for parent, rounds in groups.items():
        latest = max(rounds, key=lambda x: (x.get("date", ""), x["url"]))
        best = min(rounds, key=lambda x: REVIEW_RANK.get(x.get("review_state"), 9))
        rec = dict(latest)
        rec["review_state"] = best.get("review_state")
        rec["rounds"] = len(rounds)
        out.append(rec)
    return sort_records(out)


def _type_label(rec):
    if rec["type"] == "review":
        label = REVIEW_LABELS.get(rec.get("review_state"), "Review")
        if rec.get("rounds", 1) > 1:
            label += f" ({rec['rounds']} rounds)"
        return label
    if rec["type"] == "comment":
        return "Comment on PR" if rec.get("on") == "pr" else "Comment"
    if rec["type"] == "extra":
        return EXTRA_LABELS.get(rec.get("kind"), rec.get("kind", "Other").capitalize())
    if rec["type"] == "paper":
        return scholar.kind_label(rec)
    if rec["type"] == "peer_review":
        return "Peer review"
    label = TYPE_LABELS.get((rec["type"], rec.get("state")), rec["type"])
    if rec.get("merged"):
        label += f" {rec['merged']}"
    return label


def _cell(text):
    return (text or "").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def plural_word(word, n=2):
    if n == 1:
        return word
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    return word + "s"


def _plural(n, word):
    return f"{n} {plural_word(word, n)}"


def _names(names, limit=4):
    """'A', 'A and B', 'A, B and C', or 'A, B, C, D and 2 more'."""
    names = list(names)
    if not names:
        return ""
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" and {len(names) - limit} more"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _short_ref(url, home_repo):
    m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)", url)
    if not m:
        return url
    return f"#{m.group(2)}" if m.group(1) == home_repo else f"{m.group(1)}#{m.group(2)}"


NON_GITHUB = ("extra", "paper", "peer_review")


def github_records(records):
    return [r for r in records if r["type"] not in NON_GITHUB]


def summary_counts(records):
    gh_records = github_records(records)
    others_comments = [r for r in gh_records if r["type"] == "comment" and not r.get("on_own_item")]
    return {
        "merged": sum(1 for r in gh_records if r["type"] == "pr" and r.get("state") == "merged"),
        "open_prs": sum(1 for r in gh_records if r["type"] == "pr" and r.get("state") == "open"),
        "reviews": len({r.get("parent_url") or r["url"] for r in gh_records if r["type"] == "review"}),
        "review_rounds": sum(1 for r in gh_records if r["type"] == "review"),
        "comments": len(others_comments),
        "issues": sum(1 for r in gh_records if r["type"] == "issue"),
        "components": sorted({r["component"] for r in gh_records if r["component"] != "general"
                              and r["type"] in ("pr", "review")}),
        "repos": sorted({r["repo"] for r in gh_records}),
        "since": min((r["date"] for r in gh_records if r.get("date")), default=""),
        "extras": sum(1 for r in records if r["type"] == "extra"),
        "papers": sum(1 for r in records if r["type"] == "paper"),
        "citations": sum(r.get("citations", 0) for r in records if r["type"] == "paper"),
        "peer_reviews": sum(1 for r in records if r["type"] == "peer_review"),
        "review_venues": sorted({r.get("venue", "") for r in records if r["type"] == "peer_review"} - {""}),
    }


def _visible(records, cfg):
    out = []
    for r in records:
        if r["type"] == "comment" and r.get("on_own_item") and not cfg["readme_include_own_comments"]:
            continue
        if r["type"] == "pr" and r.get("state") == "closed" and cfg["readme_hide_closed_prs"]:
            continue
        out.append(r)
    return out


def merged_line(r):
    parts = [f"**[#{r['number']} {_cell(r['title'])}]({r['url']})**"]
    if r["component"] != "general":
        parts.append(f"`{_cell(r['component'])}`")
    merged = f"merged {r.get('merged', '')}"
    if r.get("merged_by"):
        merged += f" by {r['merged_by']}"
    parts.append(merged)
    approvers = [a for a in r.get("approved_by", []) if a != r.get("merged_by")]
    if approvers:
        parts.append(f"approved by {_names(approvers)}")
    if "additions" in r:
        parts.append(f"+{r['additions']} −{r['deletions']} in {_plural(r.get('files', 0), 'file')}")
    if r.get("fixes"):
        parts.append("fixes " + ", ".join(f"[{_short_ref(u, r['repo'])}]({u})" for u in r["fixes"]))
    line = "- " + " · ".join(parts)
    if r.get("note"):
        line += f"  \n  _{_cell(r['note'])}_"
    return line


def render_section(records, cfg):
    shown = collapse_reviews(_visible(records, cfg))
    gh_shown = github_records(shown)
    extras = sort_records([r for r in records if r["type"] == "extra"])
    c = summary_counts(records)
    lines = [START, f"## {cfg['readme_title']}", ""]
    if not records:
        lines += ["_Nothing yet._", "", END]
        return "\n".join(lines) + "\n"

    headline = [f"**{_plural(c['merged'], 'merged PR')}**"]
    if c["open_prs"]:
        headline.append(f"{c['open_prs']} open")
    headline += [f"**{_plural(c['reviews'], 'PR')} reviewed**",
                 f"{_plural(c['comments'], 'comment')} on others' issues and PRs",
                 _plural(c["issues"], "issue") + " opened"]
    lines.append(" · ".join(headline))
    context = []
    if c["since"]:
        context.append(f"Active since {date.fromisoformat(c['since']).strftime('%b %Y')} "
                       f"across {_plural(len(c['repos']), 'repository')}")
    if c["components"]:
        context.append("components " + ", ".join(f"`{x}`" for x in c["components"]))
    if context:
        lines += ["", " · ".join(context)]
    research = []
    if c["papers"]:
        research.append(_plural(c["papers"], "publication")
                        + (f" ({_plural(c['citations'], 'citation')})" if c["citations"] else ""))
    if c["peer_reviews"]:
        research.append(_plural(c["peer_reviews"], "peer review")
                        + (f" for {_names(c['review_venues'], 3)}" if c["review_venues"] else ""))
    if research:
        lines += ["", " · ".join(research)]
    links = profile_links(cfg)
    if links:
        lines += ["", " · ".join(f"[{name}]({url})" for name, url in links)]
    lines.append("")

    merged = sort_records([r for r in gh_shown if r["type"] == "pr" and r.get("state") == "merged"])
    if merged:
        lines += ["### Merged", ""] + [merged_line(r) for r in merged] + [""]

    recent = [r for r in sort_records(gh_shown) if not (r["type"] == "pr" and r.get("state") == "merged")]
    recent = recent[:cfg["readme_recent_items"]]
    if recent:
        lines += ["### Recent activity", ""]
        for r in recent:
            repo = r["repo"].split("/")[-1]
            lines.append(f"- {r['date']} · {_type_label(r)} · [#{r['number']} {_cell(r['title'])}]({r['url']}) · {repo}")
        lines.append("")

    papers = sort_records([r for r in records if r["type"] == "paper"])
    if papers:
        lines += ["### Publications", ""]
        for r in papers:
            venue = f" · _{_cell(r['venue'])}_" if r.get("venue") else ""
            cited = f" · cited by {r['citations']}" if r.get("citations") else ""
            lines.append(f"- {r.get('date_label') or r['date'][:4]} · [{_cell(r['title'])}]({r['url']}){venue}{cited}")
        lines.append("")

    reviews = [r for r in records if r["type"] == "peer_review"]
    if reviews:
        lines += ["### Peer review", ""]
        by_venue = {}
        for r in reviews:
            by_venue.setdefault(r.get("venue") or "Unspecified venue", []).append(r)
        for venue in sorted(by_venue, key=lambda v: (-len(by_venue[v]), v)):
            years = sorted({(r.get("date") or "")[:4] for r in by_venue[venue]} - {""})
            span = f" ({years[0]}–{years[-1]})" if len(years) > 1 else (f" ({years[0]})" if years else "")
            lines.append(f"- **{_cell(venue)}** · {_plural(len(by_venue[venue]), 'review')}{span}")
        lines.append("")

    if extras:
        lines += ["### Beyond GitHub", ""]
        for r in extras:
            title = f"[{_cell(r['title'])}]({r['link']})" if r.get("link") else _cell(r["title"])
            note = f" — {_cell(r['note'])}" if r.get("note") else ""
            lines.append(f"- {r['date']} · {_type_label(r)} · {title}{note}")
        lines.append("")

    has_notes = any(r.get("note") for r in gh_shown)
    lines += ["<details>", "<summary><b>All activity</b>, by repository and component</summary>", ""]
    by_repo = {}
    for r in gh_shown:
        by_repo.setdefault(r["repo"], []).append(r)
    for repo in sorted(by_repo, key=lambda k: (-len(by_repo[k]), k)):
        lines += [f"#### {repo}", ""]
        by_comp = {}
        for r in by_repo[repo]:
            by_comp.setdefault(r["component"], []).append(r)
        for comp in sorted(by_comp, key=lambda k: (k == "general", -len(by_comp[k]), k)):
            items = sort_records(by_comp[comp])
            counts = [(t, sum(1 for r in items if r["type"] == t)) for t in ("pr", "review", "comment", "issue")]
            words = {"pr": "PR", "review": "PR", "comment": "comment", "issue": "issue"}
            parts = [_plural(n, words[t]) + (" reviewed" if t == "review" else "") for t, n in counts if n]
            lines += ["<details>", f"<summary><b>{_cell(comp)}</b> · {' · '.join(parts)}</summary>", ""]
            lines += ["| Date | Type | Item |" + (" Note |" if has_notes else ""),
                      "|---|---|---|" + ("---|" if has_notes else "")]
            for r in items:
                tags = f" `{'` `'.join(r['tags'])}`" if cfg["readme_show_tags"] and r.get("tags") else ""
                row = f"| {r['date']} | {_type_label(r)} | [#{r['number']} {_cell(r['title'])}]({r['url']}) |"
                if has_notes:
                    row += f" {_cell(r.get('note', ''))}{tags} |"
                lines.append(row)
            lines += ["", "</details>", ""]
    lines += ["</details>", "",
              f"_Rebuilt daily from GitHub's public record by [oss-scout]({cfg['tool_url']})._", END]
    return "\n".join(lines) + "\n"


def profile_links(cfg):
    """[(name, url)] for the links line: LinkedIn, ORCID and anything else configured."""
    links = dict(cfg.get("links") or {})
    if cfg.get("orcid") and "ORCID" not in links:
        links["ORCID"] = f"https://orcid.org/{cfg['orcid']}"
    order = {"LinkedIn": 0, "ORCID": 1}
    return sorted(((str(k), str(v)) for k, v in links.items() if v), key=lambda kv: (order.get(kv[0], 9), kv[0]))


def replace_section(readme, section):
    if START in readme and END in readme:
        before = readme.split(START, 1)[0]
        after = readme.split(END, 1)[1]
        return before + section.rstrip("\n") + after
    return readme.rstrip("\n") + ("\n\n" if readme.strip() else "") + section


# ---------------------------------------------------------------- run

def run(gh, cfg, today, full=False, dry_run=False, base_dir=".", scholar_fetch=None):
    """Collect, annotate, render and write. Returns (changed_files, errors, records)."""
    data_path = os.path.join(base_dir, cfg["data_path"])
    readme_path = os.path.join(base_dir, cfg["readme_path"])
    previous = []
    if os.path.exists(data_path):
        with open(data_path, encoding="utf-8") as fh:
            previous = json.load(fh).get("records", [])
    existing = [r for r in previous if r.get("type") not in NON_GITHUB]
    old_research = [r for r in previous if r.get("type") in ("paper", "peer_review")]

    rebuild = full or not existing
    records, errors = collect(gh, cfg, existing, today, full=rebuild)
    if errors and rebuild and existing:
        # A partial rebuild would drop real history; keep the files as they are.
        return [], errors, existing
    research, research_errors = scholar.collect(cfg, **({"fetch": scholar_fetch} if scholar_fetch else {}))
    if research_errors and not research:
        research = old_research          # ORCID unreachable today: keep what we had
    errors += research_errors
    notes = load_notes_file(os.path.join(base_dir, cfg["annotations_path"]))
    records = sort_records(apply_annotations(records + research, parse_url_notes(notes)) + parse_extras(notes))

    data_text = json.dumps({"user": cfg["github_user"], "records": records},
                           indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    readme_old = ""
    if os.path.exists(readme_path):
        with open(readme_path, encoding="utf-8") as fh:
            readme_old = fh.read()
    readme_new = replace_section(readme_old, render_section(records, cfg))

    changed = []
    for path, new in ((data_path, data_text), (readme_path, readme_new)):
        old = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                old = fh.read()
        if old != new:
            changed.append(path)
            if not dry_run:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
    return changed, errors, records
