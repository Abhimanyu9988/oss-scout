"""A private, categorised portfolio built from your public contribution log.

Inputs:
  - contributions.json from the contribution log (public data)
  - a notes file you keep private: your own categories, rules that file each kind of
    contribution under a category, per-item tags and notes, and entries that never
    appear publicly

Output: a Markdown portfolio (and optionally CSV) with an at-a-glance summary,
CV-ready lines, every item filed under your categories with its evidence (size,
approvals, who merged it, what it fixed, words of recognition), the people you've
worked with, and a month-by-month timeline. Deterministic: same input, same output.
"""

import csv
import io
import json
from datetime import date

from .contrib_log import (_names, _plural, _short_ref, _type_label, collapse_reviews, github_records, load_notes_file,
                          parse_extras, parse_url_notes, sort_records)


def kind_key(rec):
    """The key used by `auto:` rules: pr_merged, pr_open, pr_closed, review, comment,
    comment_own, issue, or extra:<type>."""
    t = rec["type"]
    if t == "pr":
        return f"pr_{rec.get('state', 'open')}"
    if t == "comment":
        return "comment_own" if rec.get("on_own_item") else "comment"
    if t == "extra":
        return f"extra:{rec.get('kind', 'other')}"
    return t


def load_portfolio_notes(path):
    data = load_notes_file(path)
    items = parse_url_notes(data.get("items", {}) or {})
    items.update(parse_url_notes(data))                     # URL keys at the top level work too
    categories = {}
    for key, value in (data.get("categories", {}) or {}).items():
        if isinstance(value, str):
            value = {"title": value}
        value = value or {}
        categories[str(key)] = {"title": str(value.get("title") or key),
                                "description": str(value.get("description", "") or "").strip()}
    return {
        "categories": categories,
        "auto": {str(k): str(v) for k, v in (data.get("auto", {}) or {}).items()},
        "items": items,
        "extras": parse_extras(data),
        "org_names": {str(k): str(v) for k, v in (data.get("org_names", {}) or {}).items()},
    }


def categorise(records, notes):
    """Each record gets `categories`: its auto rule, its public tags and its private tags."""
    known = notes["categories"]
    out = []
    for rec in records:
        rec = dict(rec)
        cats = []
        auto = notes["auto"].get(kind_key(rec))
        if auto:
            cats.append(auto)
        cats += [t for t in rec.get("tags", []) if t in known]
        private = notes["items"].get(rec["url"], {})
        cats += [t for t in private.get("tags", []) if t not in cats]
        if private.get("note"):
            rec["private_note"] = private["note"]
        rec["categories"] = [c for i, c in enumerate(cats) if c not in cats[:i]]
        out.append(rec)
    return out


def people(records, user):
    """Who you've worked with, and how. Useful when you need someone who knows your work."""
    table = {}

    def add(who, how, rec):
        if not who or who.lower() == user.lower():
            return
        link = rec.get("parent_url") or rec["url"]
        entry = table.setdefault(who, {"how": {}, "urls": [], "seen": set()})
        if (how, link) in entry["seen"]:
            return
        entry["seen"].add((how, link))
        entry["how"][how] = entry["how"].get(how, 0) + 1
        if link not in entry["urls"]:
            entry["urls"].append(link)

    for r in records:
        if r["type"] == "pr" and r.get("state") == "merged":
            add(r.get("merged_by"), "merged your PRs", r)
            for a in r.get("approved_by", []):
                add(a, "approved your PRs", r)
            for q in r.get("recognition", []):
                add(q["by"], "praised your work", r)
        if r["type"] == "review":
            add(r.get("author"), "you reviewed their PRs", r)
    rows = []
    for who, entry in table.items():
        weight = sum(entry["how"].values())
        rows.append((who, entry, weight))
    return sorted(rows, key=lambda x: (-x[2], x[0].lower()))


def _org_label(repos, org_names):
    owners = sorted({r.split("/")[0] for r in repos})
    return ", ".join(org_names.get(o, o) for o in owners)


def cv_lines(records, org_names):
    gh = github_records(records)
    merged = [r for r in gh if r["type"] == "pr" and r.get("state") == "merged"]
    reviews = [r for r in gh if r["type"] == "review"]
    comments = [r for r in gh if r["type"] == "comment" and not r.get("on_own_item")]
    repos = sorted({r["repo"] for r in gh})
    lines = []
    if not gh:
        return lines
    since = min(r["date"] for r in gh if r.get("date"))
    org = _org_label(repos, org_names)
    if merged:
        comps = sorted({r["component"] for r in merged if r["component"] != "general"})
        maintainers = sorted({r.get("merged_by") for r in merged if r.get("merged_by")}
                             | {a for r in merged for a in r.get("approved_by", [])})
        line = (f"Active in {org} since {date.fromisoformat(since).strftime('%B %Y')}: "
                f"{_plural(len(merged), 'merged pull request')}")
        if comps:
            line += f" in {_names(comps, 4)}"
        if maintainers:
            line += f", reviewed and accepted by {_plural(len(maintainers), 'maintainer')}"
        lines.append(line + ".")
        fixed = sorted({u for r in merged for u in r.get("fixes", [])})
        if fixed:
            lines.append(f"Resolved {_plural(len(fixed), 'reported issue')} through merged fixes.")
    if reviews:
        authors = sorted({r.get("author") for r in reviews if r.get("author")})
        prs = sorted({r["parent_url"] for r in reviews})
        line = f"Reviewed {_plural(len(prs), 'pull request')} from {_plural(len(authors), 'other contributor')}"
        comps = sorted({r["component"] for r in reviews if r["component"] != "general"})
        if comps:
            line += f" ({_names(comps, 4)})"
        lines.append(line + ".")
    if comments:
        threads = sorted({r["parent_url"] for r in comments})
        lines.append(f"Contributed to {_plural(len(threads), 'technical discussion')} across "
                     f"{_plural(len({r['repo'] for r in comments}), 'repository')}.")
    papers = [r for r in records if r["type"] == "paper"]
    if papers:
        venues = sorted({r["venue"] for r in papers if r.get("venue")})
        cites = sum(r.get("citations", 0) for r in papers)
        line = f"Published {_plural(len(papers), 'peer-reviewed paper')}"
        if venues:
            line += f" in {_names(venues, 3)}"
        if cites:
            line += f", cited {_plural(cites, 'time')}"
        lines.append(line + ".")
    peer = [r for r in records if r["type"] == "peer_review"]
    if peer:
        venues = sorted({r["venue"] for r in peer if r.get("venue")})
        lines.append(f"Completed {_plural(len(peer), 'peer review')} for {_names(venues, 3) or 'journals and conferences'}.")
    talks = [r for r in records if r["type"] == "extra" and r.get("kind") in ("talk", "workshop", "podcast")]
    if talks:
        lines.append(f"Gave {_plural(len(talks), 'talk')} on this work.")
    pcs = [r for r in records if r["type"] == "extra" and r.get("kind") == "review"]
    if pcs:
        lines.append(f"Served on {_plural(len(pcs), 'program committee')} or review panel.")
    return lines


def _txt(text):
    return (text or "").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _ref(url):
    """'opentelemetry-collector-contrib#51657' for a GitHub issue or PR URL."""
    parts = url.split("/")
    if len(parts) > 6 and parts[2] == "github.com":
        return f"{parts[4]}#{parts[6].split('#')[0]}"
    return url


def _item_line(r):
    if r["type"] == "paper":
        venue = f" · _{_txt(r['venue'])}_" if r.get("venue") else ""
        cited = f" · cited by {r['citations']}" if r.get("citations") else ""
        return f"- {r.get('date_label') or r['date'][:4]} · {_type_label(r)} · [{_txt(r['title'])}]({r['url']}){venue}{cited}"
    if r["type"] == "peer_review":
        return f"- {r.get('date_label') or r['date'][:4]} · Peer review · {_txt(r.get('venue', ''))}"
    if r["type"] == "extra":
        title = f"[{_txt(r['title'])}]({r['link']})" if r.get("link") else _txt(r["title"])
        return f"- {r['date']} · {_type_label(r)} · {title}"
    repo = r["repo"].split("/")[-1]
    comp = f" · `{r['component']}`" if r["component"] != "general" else ""
    return f"- {r['date']} · {_type_label(r)} · [#{r['number']} {_txt(r['title'])}]({r['url']}) · {repo}{comp}"


def _evidence(r):
    out = []
    if "additions" in r:
        bits = [f"+{r['additions']} −{r['deletions']} in {_plural(r.get('files', 0), 'file')}"]
        if r.get("discussion"):
            bits.append(f"{_plural(r['discussion'], 'comment')} in review")
        if r.get("merged_by"):
            bits.append(f"merged by {r['merged_by']}")
        if r.get("approved_by"):
            bits.append(f"approved by {_names(r['approved_by'])}")
        if r.get("fixes"):
            bits.append("fixes " + ", ".join(f"[{_short_ref(u, r['repo'])}]({u})" for u in r["fixes"]))
        out.append("  - " + " · ".join(bits))
    for q in r.get("recognition", []):
        out.append(f"  - “{_txt(q['text'])}” — {q['by']} ([link]({q['url']}))")
    if r.get("note"):
        out.append(f"  - Public note: {_txt(r['note'])}")
    if r.get("private_note"):
        out.append(f"  - Note: {_txt(r['private_note'])}")
    return out


def render(records, notes, user):
    records = sort_records(records)
    gh = github_records(records)
    merged = [r for r in gh if r["type"] == "pr" and r.get("state") == "merged"]
    reviews = [r for r in gh if r["type"] == "review"]
    comments = [r for r in gh if r["type"] == "comment" and not r.get("on_own_item")]
    recognition = sorted(((q, r) for r in merged for q in r.get("recognition", [])),
                         key=lambda x: (x[0]["date"], x[0]["url"]), reverse=True)
    crowd = people(records, user)
    latest = max((r["date"] for r in records if r.get("date")), default="")

    lines = [f"# Contribution portfolio · {user}", ""]
    lines += [f"Data through {latest or 'n/a'}. Built from the public contribution log and your private notes. "
              "Keep this repository private.", ""]

    lines += ["## At a glance", "", "| | |", "|---|---|"]
    adds = sum(r.get("additions", 0) for r in merged)
    dels = sum(r.get("deletions", 0) for r in merged)
    lines.append(f"| Merged pull requests | {len(merged)}" + (f" (+{adds} −{dels})" if merged else "") + " |")
    lines.append(f"| Reviews of others' pull requests | {len(reviews)} on "
                 f"{_plural(len({r['parent_url'] for r in reviews}), 'PR')} by "
                 f"{_plural(len({r.get('author') for r in reviews if r.get('author')}), 'author')} |")
    lines.append(f"| Comments on others' issues and PRs | {len(comments)} |")
    lines.append(f"| Issues opened | {sum(1 for r in gh if r['type'] == 'issue')} |")
    lines.append(f"| Repositories | {len({r['repo'] for r in gh})} |")
    comps = sorted({r['component'] for r in gh if r['component'] != 'general' and r['type'] in ('pr', 'review')})
    lines.append(f"| Components | {', '.join(comps) or '—'} |")
    lines.append(f"| Maintainers who approved or merged your work | "
                 f"{len({p for p, e, _ in crowd if {'merged your PRs', 'approved your PRs'} & set(e['how'])})} |")
    lines.append(f"| Words of recognition | {len(recognition)} |")
    papers = [r for r in records if r["type"] == "paper"]
    if papers:
        lines.append(f"| Publications | {len(papers)} ({_plural(sum(r.get('citations', 0) for r in papers), 'citation')}) |")
    peer = [r for r in records if r["type"] == "peer_review"]
    if peer:
        lines.append(f"| Peer reviews | {len(peer)} for "
                     f"{_plural(len({r.get('venue') for r in peer}), 'venue')} |")
    lines.append(f"| Beyond GitHub | {sum(1 for r in records if r['type'] == 'extra')} |")
    lines.append("")

    cv = cv_lines(records, notes["org_names"])
    if cv:
        lines += ["## CV-ready summary", ""] + [f"- {c}" for c in cv] + [""]

    lines += ["## By category", ""]
    listed = collapse_reviews(records)
    for key, cat in notes["categories"].items():
        items = [r for r in listed if key in r.get("categories", [])]
        lines += [f"### {cat['title']} ({len(items)})", ""]
        if cat["description"]:
            lines += [f"_{cat['description']}_", ""]
        if not items:
            lines += ["_Nothing filed here yet._", ""]
            continue
        for r in items:
            lines.append(_item_line(r))
            lines += _evidence(r)
        lines.append("")

    if recognition:
        lines += ["## Recognition from peers", ""]
        for q, r in recognition:
            lines.append(f"- {q['date']} · {q['by']} on [#{r['number']} {_txt(r['title'])}]({r['url']}): "
                         f"“{_txt(q['text'])}” ([link]({q['url']}))")
        lines.append("")

    if crowd:
        lines += ["## People you've worked with", "",
                  "Maintainers and contributors who know your work first-hand.", "",
                  "| Person | How | Items |", "|---|---|---|"]
        for who, entry, _ in crowd:
            how = " · ".join(f"{h} ({n})" for h, n in sorted(entry["how"].items()))
            links = ", ".join(f"[{_ref(u)}]({u})" for u in entry["urls"][:5])
            lines.append(f"| [{who}](https://github.com/{who}) | {how} | {links} |")
        lines.append("")

    lines += ["## Timeline", "",
              "| Month | PRs opened | PRs merged | PRs reviewed | Comments | Issues | Papers & reviews | Beyond GitHub |",
              "|---|---|---|---|---|---|---|---|"]
    def month(r):
        return r.get("date", "")[:7]

    months = sorted({month(r) for r in records if month(r)} |
                    {r["merged"][:7] for r in records if r.get("merged")}, reverse=True)
    for m in months:
        in_month = [r for r in records if month(r) == m]
        row = [sum(1 for r in in_month if r["type"] == "pr"),
               sum(1 for r in records if r["type"] == "pr" and (r.get("merged") or "").startswith(m)),
               len({r.get("parent_url") for r in in_month if r["type"] == "review"}),
               sum(1 for r in in_month if r["type"] == "comment" and not r.get("on_own_item")),
               sum(1 for r in in_month if r["type"] == "issue"),
               sum(1 for r in in_month if r["type"] in ("paper", "peer_review")),
               sum(1 for r in in_month if r["type"] == "extra")]
        lines.append(f"| {m} | " + " | ".join(str(x) for x in row) + " |")
    lines.append("")

    lines += linkedin_section(records, notes, latest)

    loose = [r for r in listed if not r.get("categories") and kind_key(r) not in ("comment_own", "pr_closed")]
    if loose:
        lines += [f"## Not yet in a category ({len(loose)})", "",
                  "Add a tag in your notes file, or an `auto:` rule, to file these.", ""]
        lines += [_item_line(r) for r in loose] + [""]
    return "\n".join(lines).rstrip() + "\n"


def linkedin_section(records, notes, latest, merged_window=30, paper_window=90):
    """Paste-ready text for LinkedIn: an About paragraph and drafts for recent milestones.
    Recent is measured from the newest item in the data, so the output is deterministic."""
    gh = github_records(records)
    merged = sort_records([r for r in gh if r["type"] == "pr" and r.get("state") == "merged"])
    reviewed = {r["parent_url"] for r in gh if r["type"] == "review"}
    papers = sort_records([r for r in records if r["type"] == "paper"])
    peer = [r for r in records if r["type"] == "peer_review"]
    if not (merged or reviewed or papers):
        return []
    org = _org_label({r["repo"] for r in gh}, notes["org_names"]) if gh else ""
    comps = sorted({r["component"] for r in merged if r["component"] != "general"})

    about = []
    if merged or reviewed:
        bit = f"I contribute to {org}"
        if comps:
            bit += f", mostly {_names(comps, 3)}"
        about.append(bit + ".")
        parts = []
        if merged:
            parts.append(f"{_plural(len(merged), 'of my pull request')} {'has' if len(merged) == 1 else 'have'} been merged")
        if reviewed:
            parts.append(f"I've reviewed {_plural(len(reviewed), 'pull request')} from other contributors")
        about.append(" and ".join(parts) + ".")
    if papers:
        venues = sorted({r["venue"] for r in papers if r.get("venue")})
        about.append(f"I've published {_plural(len(papers), 'paper')}" + (f" in {_names(venues, 3)}" if venues else "")
                     + (" and review for " + _names(sorted({r['venue'] for r in peer if r.get('venue')}), 3) if peer else "")
                     + ".")

    lines = ["## For LinkedIn", "", "Drafts to edit and post yourself. LinkedIn doesn't allow automated updates.", "",
             "### About (draft)", "", " ".join(about), ""]

    def recent(day, window):
        if not (day and latest):
            return False
        return (date.fromisoformat(latest[:10]) - date.fromisoformat(day[:10])).days <= window

    drafts = []
    for r in merged:
        if not recent(r.get("merged"), merged_window):
            continue
        where = f"`{r['component']}` in " if r["component"] != "general" else ""
        text = f"My change to {where}{r['repo'].split('/')[-1]} was merged: {r['title']}."
        if r.get("fixes"):
            text += f" It closes {_plural(len(r['fixes']), 'reported issue')}."
        thanks = sorted(set(r.get("approved_by", [])) | ({r["merged_by"]} if r.get("merged_by") else set()))
        if thanks:
            text += f" Thanks to {_names(thanks)} for the reviews."
        drafts.append(f"- {text} {r['url']}")
    for r in papers:
        if recent(r.get("date"), paper_window):
            drafts.append(f"- New paper" + (f" in {r['venue']}" if r.get("venue") else "") + f": {r['title']}. {r['url']}")
    if drafts:
        lines += ["### Post drafts", ""] + drafts + [""]
    return lines


CSV_FIELDS = ["date", "type", "state", "repo", "component", "number", "title", "url", "categories",
              "note", "private_note", "additions", "deletions", "files", "merged_by", "approved_by",
              "fixes", "recognition"]


def to_csv(records):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in sort_records(records):
        row = {k: r.get(k, "") for k in CSV_FIELDS}
        row["type"] = _type_label(r) if r["type"] != "pr" else "PR"
        row["state"] = r.get("state", r.get("kind", ""))
        row["categories"] = ";".join(r.get("categories", []))
        row["approved_by"] = ";".join(r.get("approved_by", []))
        row["fixes"] = ";".join(r.get("fixes", []))
        row["recognition"] = len(r.get("recognition", []))
        w.writerow(row)
    return buf.getvalue()


def build(data_path, notes_path):
    with open(data_path, encoding="utf-8") as fh:
        data = json.load(fh)
    notes = load_portfolio_notes(notes_path)
    records = list(data.get("records", [])) + notes["extras"]
    return categorise(records, notes), notes, data.get("user", "")


def write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True

