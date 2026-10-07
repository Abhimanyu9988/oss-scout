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
                           date=_day(rv.get("submitted_at")), review_state=rv["state"].lower())
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

def load_annotations(path):
    """{url: {"note": str, "tags": [..]}} from YAML or JSON; {} if the file is missing."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not re.search(r"^\s*[^#\s]", text, re.M):
        return {}  # only comments
    if path.endswith(".json"):
        data = json.loads(text)
    else:
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            raise SystemExit(f"{path} needs PyYAML: pip install pyyaml (or use annotations.json)") from None
        data = yaml.safe_load(text) or {}
    out = {}
    for url, value in data.items():
        if isinstance(value, str):
            value = {"note": value}
        out[str(url).strip()] = {"note": str(value.get("note", "")).strip(),
                                 "tags": [str(t) for t in value.get("tags", []) or []]}
    return out


def apply_annotations(records, annotations):
    """Annotations match a record by its exact URL (a PR, an issue, a review or a comment)."""
    out = []
    for rec in records:
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


def _type_label(rec):
    if rec["type"] == "review":
        return REVIEW_LABELS.get(rec.get("review_state"), "Review")
    if rec["type"] == "comment":
        return "Comment on PR" if rec.get("on") == "pr" else "Comment"
    label = TYPE_LABELS.get((rec["type"], rec.get("state")), rec["type"])
    if rec.get("merged"):
        label += f" {rec['merged']}"
    return label


def _cell(text):
    return (text or "").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def summary_counts(records):
    others_comments = [r for r in records if r["type"] == "comment" and not r.get("on_own_item")]
    return {
        "merged": sum(1 for r in records if r["type"] == "pr" and r.get("state") == "merged"),
        "open_prs": sum(1 for r in records if r["type"] == "pr" and r.get("state") == "open"),
        "reviews": sum(1 for r in records if r["type"] == "review"),
        "comments": len(others_comments),
        "issues": sum(1 for r in records if r["type"] == "issue"),
        "components": sorted({r["component"] for r in records if r["component"] != "general"
                              and r["type"] in ("pr", "review")}),
    }


def render_section(records, cfg):
    shown = [r for r in records if cfg["readme_include_own_comments"]
             or not (r["type"] == "comment" and r.get("on_own_item"))]
    c = summary_counts(records)
    has_notes = any(r.get("note") for r in shown)
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
    if c["components"]:
        lines += ["", "Components: " + ", ".join(f"`{x}`" for x in c["components"])]
    lines.append("")

    by_repo = {}
    for r in shown:
        by_repo.setdefault(r["repo"], []).append(r)
    for repo in sorted(by_repo, key=lambda k: (-len(by_repo[k]), k)):
        lines += [f"### {repo}", ""]
        by_comp = {}
        for r in by_repo[repo]:
            by_comp.setdefault(r["component"], []).append(r)
        for comp in sorted(by_comp, key=lambda k: (k == "general", -len(by_comp[k]), k)):
            items = sort_records(by_comp[comp])
            n_pr = sum(1 for r in items if r["type"] == "pr")
            n_rv = sum(1 for r in items if r["type"] == "review")
            n_cm = sum(1 for r in items if r["type"] == "comment")
            n_is = sum(1 for r in items if r["type"] == "issue")
            parts = [p for p in (n_pr and _plural(n_pr, "PR"), n_rv and _plural(n_rv, "review"),
                                 n_cm and _plural(n_cm, "comment"), n_is and _plural(n_is, "issue")) if p]
            lines += ["<details>", f"<summary><b>{_cell(comp)}</b> · {' · '.join(parts)}</summary>", ""]
            header = "| Date | Type | Item |" + (" Note |" if has_notes else "")
            lines += [header, "|---|---|---|" + ("---|" if has_notes else "")]
            for r in items:
                title = f"#{r['number']} {_cell(r['title'])}"
                tags = f" `{'` `'.join(r['tags'])}`" if cfg["readme_show_tags"] and r.get("tags") else ""
                row = f"| {r['date']} | {_type_label(r)} | [{title}]({r['url']}) |"
                if has_notes:
                    row += f" {_cell(r.get('note', ''))}{tags} |"
                lines.append(row)
            lines += ["", "</details>", ""]
    lines += [f"_Rebuilt daily from GitHub's public record by [oss-scout]({cfg['tool_url']})._", END]
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
            existing = json.load(fh).get("records", [])

    rebuild = full or not existing
    records, errors = collect(gh, cfg, existing, today, full=rebuild)
    if errors and rebuild and existing:
        # A partial rebuild would drop real history; keep the files as they are.
        return [], errors, existing
    records = apply_annotations(records, load_annotations(os.path.join(base_dir, cfg["annotations_path"])))

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
