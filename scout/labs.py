"""Upstream bugs and enhancements found in your own lab write-ups.

When you publish hands-on articles with their raw lab output (config "labs"), each new or changed
article is read once by the model, with its lab's README and the main logs. It looks for things worth
filing upstream in the repos and labels you watch: a bug the output shows, or a missing feature the
write-up had to work around. A finding is kept only if its quoted evidence appears word for word in
the article or the logs, and only for a repo and label in your config. Then upstream is searched for an
issue that may already cover it.

Findings go on the board with a draft issue you can file yourself. oss-scout never files them: it
only reads other people's repos. Findings are cached per article until the article or its output
changes, and each run reads at most `labs.max_calls` articles.
"""

import base64
import hashlib
import json
import re

from .ai import AIError
from .github import GitHubError

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_FINDINGS = 3
ARTICLE_CHARS = 30000
MAIN_LOG_CHARS = 14000
README_CHARS = 6000
MAIN_LOG = re.compile(r"(reproduce|fix|check)\.log$")
KINDS = ("bug", "enhancement")

SYSTEM = """You read an engineer's hands-on lab article and the lab's raw output, and find things worth
filing as an issue in the open-source projects listed under AREAS. Only two kinds count:
bug: the output shows a component behaving differently from its docs, its config or another source
  of truth in the same run (for example two counts that should match and don't).
enhancement: the article had to work around something the component doesn't offer, or states a
  measurement it couldn't make, and the component could reasonably add it.
Skip anything about the lab's own scripts, cloud setup, the article's writing, or projects not in AREAS.
Never invent: "evidence" must be copied exactly, character for character, from the ARTICLE or a LOG.
Answer with exactly one JSON object and nothing else:
{"findings": [{"kind": "bug" | "enhancement", "repo": "<owner/name from AREAS>",
  "label": "<one label from AREAS for that repo>", "title": "<issue title, at most 90 characters>",
  "evidence": "<exact quote, at most 300 characters>", "why": "<at most 40 words>",
  "body": "<a draft issue body in markdown: what was observed, the exact evidence, versions from the
  article, what is asked for; at most 250 words; no greetings>"}]}
At most 3 findings, the strongest first. An empty list is a good answer when nothing qualifies."""


def _squash(text):
    return " ".join(str(text or "").split())


def areas(cfg):
    """{repo: set(labels)} from the config's watched repos. An empty set means any label."""
    return {r["repo"]: set(r.get("labels") or []) for r in cfg.get("repos") or []}


def _text(gh, repo, path):
    data, _ = gh._get(f"{gh.api}/repos/{repo}/contents/{path}")
    return base64.b64decode(data.get("content") or "").decode("utf-8", "replace")


def _listing(gh, repo, path):
    try:
        data, _ = gh._get(f"{gh.api}/repos/{repo}/contents/{path}")
    except GitHubError as err:
        if getattr(err, "status", None) == 404:
            return []
        raise
    return data if isinstance(data, list) else []


def build_prompt(cfg, slug, article, readme, logs):
    lines = ["AREAS:"]
    for repo, labels in sorted(areas(cfg).items()):
        lines.append(f"- {repo}: " + (", ".join(sorted(labels)) if labels else "any label"))
    lines += ["", f"ARTICLE ({slug}):", article[:ARTICLE_CHARS], "", "LAB README:", readme[:README_CHARS]]
    for name, text in logs.items():
        lines += ["", f"LOG {name}:", text[-MAIN_LOG_CHARS:]]
    return "\n".join(lines)


def parse_findings(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AIError("no JSON in the model's answer")
    try:
        out = json.loads(text[start:end + 1])
    except ValueError:
        raise AIError("unreadable JSON in the model's answer") from None
    found = out.get("findings")
    if not isinstance(found, list):
        raise AIError("no findings list in the model's answer")
    return [f for f in found if isinstance(f, dict)]


def keep(findings, cfg, sources):
    """Only findings in your areas whose evidence is really in the article or the logs."""
    watched, haystack, out = areas(cfg), _squash(" ".join(sources)), []
    for f in findings:
        repo, label, kind = str(f.get("repo", "")), str(f.get("label", "")), str(f.get("kind", ""))
        evidence = _squash(f.get("evidence"))
        if kind not in KINDS or repo not in watched or (watched[repo] and label not in watched[repo]):
            continue
        if len(evidence) < 12 or evidence not in haystack:
            continue
        out.append({"kind": kind, "repo": repo, "label": label, "title": _squash(f.get("title"))[:90],
                    "evidence": evidence[:300], "why": _squash(f.get("why"))[:300],
                    "body": str(f.get("body") or "").strip()[:3000]})
        if len(out) == MAX_FINDINGS:
            break
    return out


STOP = set("a an and are as at be but by for from has in is it its not of on or that the this to was with "
           "when without add adds support metric metrics receiver".split())


def possible_duplicate(gh, finding):
    """The first open or closed upstream issue under the same label that shares the title's key words."""
    words = [w for w in re.findall(r"[a-z0-9_.]{3,}", finding["title"].lower()) if w not in STOP]
    words = sorted(set(words), key=len, reverse=True)[:3]
    if not words:
        return None
    label = f' label:"{finding["label"]}"' if finding["label"] else ""
    items = gh.search_issues(f'repo:{finding["repo"]} is:issue{label} ' + " ".join(words), limit=3)
    return items[0]["html_url"] if items else None


def article_url(lab_cfg, slug):
    site = lab_cfg.get("site")
    return site.format(slug=slug) if site else f"https://github.com/{lab_cfg['repo']}/blob/HEAD/{lab_cfg['articles']}/{slug}.md"


def find(gh, judge, cfg, cache, errors):
    """Returns (findings, new_cache). findings: dicts with slug, url and the kept fields, newest article
    first. Without a judge, cached findings are still shown and nothing new is read."""
    lab_cfg = cfg.get("labs") or {}
    if not lab_cfg.get("repo") or lab_cfg.get("enabled") is False:
        return [], {}
    repo, art_dir, lab_dir = lab_cfg["repo"], lab_cfg.get("articles", "content"), lab_cfg.get("labs", "labs")
    budget = lab_cfg.get("max_calls", 3)
    try:
        articles = sorted((e for e in _listing(gh, repo, art_dir) if e.get("name", "").endswith(".md")),
                          key=lambda e: e["name"], reverse=True)
    except GitHubError as err:
        errors.append(f"lab articles in {repo}: {err}")
        return [f for v in cache.values() for f in v.get("findings", [])], cache
    new_cache, out, asked = {}, [], 0
    for entry in articles:
        slug = entry["name"][:-3]
        try:
            ev = [e for e in _listing(gh, repo, f"{lab_dir}/{slug}/evidence") if MAIN_LOG.search(e.get("name", ""))]
        except GitHubError as err:
            errors.append(f"lab output for {slug}: {err}")
            if slug in cache:
                new_cache[slug] = cache[slug]
            continue
        if not ev:
            continue                                  # no raw output, nothing to check a finding against
        sig = hashlib.sha256(" ".join([entry.get("sha", "")] + sorted(e.get("sha", "") for e in ev)).encode()).hexdigest()[:16]
        hit = cache.get(slug)
        if hit and hit.get("sig") == sig:
            new_cache[slug] = hit
        elif judge is not None and asked < budget:
            asked += 1
            try:
                article = _text(gh, repo, f"{art_dir}/{entry['name']}")
                try:
                    readme = _text(gh, repo, f"{lab_dir}/{slug}/README.md")
                except GitHubError:
                    readme = ""
                logs = {e["name"]: _text(gh, repo, f"{lab_dir}/{slug}/evidence/{e['name']}") for e in sorted(ev, key=lambda e: e["name"])}
                found = parse_findings(judge.ask_json(SYSTEM, build_prompt(cfg, slug, article, readme, logs),
                                                      model=lab_cfg.get("model", DEFAULT_MODEL), max_tokens=4000))
                kept = keep(found, cfg, [article] + list(logs.values()))
                for f in kept:
                    try:
                        f["duplicate"] = possible_duplicate(gh, f)
                    except GitHubError as err:
                        errors.append(f"duplicate search for {slug}: {err}")
                        f["duplicate"] = None
                new_cache[slug] = {"sig": sig, "findings": kept}
            except (AIError, GitHubError) as err:
                errors.append(f"lab findings for {slug}: {err}")
                if hit:
                    new_cache[slug] = hit
                continue
        elif hit:
            new_cache[slug] = hit                     # changed, but read on a later run
        for i, f in enumerate((new_cache.get(slug) or {}).get("findings", [])):
            out.append(dict(f, slug=slug, key=f"lab:{slug}:{i}", url=article_url(lab_cfg, slug)))
    return out, new_cache
