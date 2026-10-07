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
    r"\b(thanks?|thank you|great|nice|awesome|excellent|appreciate[ds]?|good catch|well done|"
    r"helpful|love (?:this|it)|kudos|brilliant|fantastic)\b", re.I)


def _is_bot(login):
    return bool(re.search(r"\[bot\]$|bot$|^github-actions|dashboard", login or "", re.I))


def _excerpt(body, limit=200):
    lines = [ln for ln in (body or "").splitlines() if ln.strip() and not ln.lstrip().startswith(">")]
    text = re.sub(r"```.*?```", " ", "\n".join(lines), flags=re.S)
    text = " ".join(text.split())
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
        if PRAISE_RE.search(c.get("body") or ""):
            recognition.append({"by": who, "text": _excerpt(c["body"]), "url": c.get("html_url", ""),
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


def _type_label(rec):
    if rec["type"] == "review":
        return REVIEW_LABELS.get(rec.get("review_state"), "Review")
    if rec["type"] == "comment":
        return "Comment on PR" if rec.get("on") == "pr" else "Comment"
    if rec["type"] == "extra":
        return EXTRA_LABELS.get(rec.get("kind"), rec.get("kind", "Other").capitalize())
    label = TYPE_LABELS.get((rec["type"], rec.get("state")), rec["type"])
    if rec.get("merged"):
        label += f" {rec['merged']}"
    return label


def _cell(text):
    return (text or "").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _names(names, limit=4):
    names = list(names)
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"


def _short_ref(url, home_repo):
    m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)", url)
    if not m:
        return url
    return f"#{m.group(2)}" if m.group(1) == home_repo else f"{m.group(1)}#{m.group(2)}"


def github_records(records):
    return [r for r in records if r["type"] != "extra"]


def summary_counts(records):
    gh_records = github_records(records)
    others_comments = [r for r in gh_records if r["type"] == "comment" and not r.get("on_own_item")]
    return {
        "merged": sum(1 for r in gh_records if r["type"] == "pr" and r.get("state") == "merged"),
        "open_prs": sum(1 for r in gh_records if r["type"] == "pr" and r.get("state") == "open"),
        "reviews": sum(1 for r in gh_records if r["type"] == "review"),
        "comments": len(others_comments),
        "issues": sum(1 for r in gh_records if r["type"] == "issue"),
        "components": sorted({r["component"] for r in gh_records if r["component"] != "general"
                              and r["type"] in ("pr", "review")}),
        "repos": sorted({r["repo"] for r in gh_records}),
        "since": min((r["date"] for r in gh_records if r.get("date")), default=""),
        "extras": sum(1 for r in records if r["type"] == "extra"),
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
    shown = _visible(records, cfg)
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
    headline += [f"**{_plural(c['reviews'], 'review')}**",
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
            words = {"pr": "PR", "review": "review", "comment": "comment", "issue": "issue"}
            parts = [_plural(n, words[t]) for t, n in counts if n]
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


def replace_section(readme, section):
    if START in readme and END in readme:
        before = readme.split(START, 1)[0]
        after = readme.split(END, 1)[1]
        return before + section.rstrip("\n") + after
    return readme.rstrip("\n") + ("\n\n" if readme.strip() else "") + section


# ---------------------------------------------------------------- run

def run(gh, cfg, today, full=False, dry_run=False, base_dir="."):
    """Collect, annotate, render and write. Returns (changed_files, errors, records)."""
    data_path = os.path.join(base_dir, cfg["data_path"])
    readme_path = os.path.join(base_dir, cfg["readme_path"])
    existing = []
    if os.path.exists(data_path):
        with open(data_path, encoding="utf-8") as fh:
            existing = [r for r in json.load(fh).get("records", []) if r.get("type") != "extra"]

    rebuild = full or not existing
    records, errors = collect(gh, cfg, existing, today, full=rebuild)
    if errors and rebuild and existing:
        # A partial rebuild would drop real history; keep the files as they are.
        return [], errors, existing
    notes = load_notes_file(os.path.join(base_dir, cfg["annotations_path"]))
    records = sort_records(apply_annotations(records, parse_url_notes(notes)) + parse_extras(notes))

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
