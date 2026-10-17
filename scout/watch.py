"""Watches: one-off things to look out for, such as an application form that opens once a cycle.

Each watch has a name, an optional date range and one or more sources. A source reports a hit
when what it looks for shows up. New hits go into the day's comment, which mentions you, and
every hit stays on the board until the watch ends.

Everything is checked with plain API calls and keyword matches, so each hit links to the place
it was found and there is nothing to second-guess.

Source types:
  github_path    {"repo": "owner/name", "path": "dir/or/file"}
                 a hit when the path exists in the repo's default branch
  github_search  {"query": "repo:owner/name some words"}
                 issues and PRs matching a GitHub search, created on or after "from"
  feed           {"url": "an RSS or Atom feed", "all": [...], "any": [...]}
                 entries whose title or summary contains every word in "all" and at least one
                 in "any" (either list may be left out), published on or after "from"

Per watch: "name", "sources", and optionally "from" and "until" (YYYY-MM-DD), "note", and
"public_issue": true to give the watch its own issue in the scout repo, which anyone can
subscribe to. That issue gets a comment for each new hit and is closed when the watch ends.
"""

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

from .github import GitHubError

MAX_FEED_BYTES = 2_000_000
ATOM = "{http://www.w3.org/2005/Atom}"


class WatchError(RuntimeError):
    pass


@dataclass
class Hit:
    key: str
    url: str
    title: str
    source: str
    when: str = ""          # YYYY-MM-DD, when known


@dataclass
class WatchResult:
    name: str
    note: str = ""
    until: str = ""
    hits: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    public: bool = False    # has its own issue in the scout repo that anyone can subscribe to
    issue_url: str = ""


def _day(value):
    return date.fromisoformat(value) if value else None


def is_active(watch, today):
    until = _day(watch.get("until"))
    return until is None or today <= until


def fetch_url(url):
    # Only https: a config pointing at file:// or a plain-http address would otherwise be read too.
    if not url.lower().startswith("https://"):
        raise WatchError("feed URL must start with https://")
    req = urllib.request.Request(url, headers={"User-Agent": "oss-scout (https://github.com/Abhimanyu9988/oss-scout)",
                                               "Accept": "application/rss+xml, application/atom+xml, application/xml, */*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(MAX_FEED_BYTES)
    except urllib.error.HTTPError as err:
        raise WatchError(f"HTTP {err.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise WatchError(f"couldn't read the feed: {getattr(err, 'reason', err)}") from None


def check_all(gh, cfg, now, fetch=None):
    """One WatchResult per active watch, in config order."""
    fetch = fetch or fetch_url
    results = []
    for watch in cfg.get("watches") or []:
        if not is_active(watch, now.date()):
            continue
        res = WatchResult(watch.get("name", "Watch"), watch.get("note", ""), watch.get("until", ""),
                          public=bool(watch.get("public_issue")))
        since = _day(watch.get("from"))
        for src in watch.get("sources") or []:
            kind = src.get("type")
            label = src.get("label") or src.get("path") or src.get("query") or src.get("url") or kind
            try:
                if kind == "github_path":
                    res.hits += _path_hits(gh, src, label)
                elif kind == "github_search":
                    res.hits += _search_hits(gh, src, since, label)
                elif kind == "feed":
                    res.hits += _feed_hits(fetch, src, since, label)
                else:
                    res.errors.append(f"{label}: unknown source type {kind!r}")
            except (GitHubError, WatchError) as err:
                res.errors.append(f"{label}: {err}")
        results.append(res)
    return results


def _path_hits(gh, src, label):
    repo, path = src["repo"], src["path"].strip("/")
    if not gh.path_exists(repo, path):
        return []
    return [Hit(f"path:{repo}/{path}", f"https://github.com/{repo}/tree/HEAD/{path}",
                f"{path} now exists in {repo}", label)]


def _search_hits(gh, src, since, label):
    query = src["query"]
    if since and "created:" not in query:
        query += f" created:>={since.isoformat()}"
    hits = []
    for item in gh.search_issues(query, limit=src.get("limit", 20)):
        repo = "/".join(item["repository_url"].rstrip("/").split("/")[-2:])
        hits.append(Hit(f"{repo}#{item['number']}", item["html_url"], item["title"], label,
                        (item.get("created_at") or "")[:10]))
    return hits


def _text(node, tag):
    found = node.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_feed(raw):
    """(title, link, published datetime or None, summary) for each RSS item or Atom entry."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise WatchError(f"not a valid feed: {err}") from None
    entries = []
    for item in root.iter("item"):                                   # RSS 2.0
        published = None
        if _text(item, "pubDate"):
            try:
                published = parsedate_to_datetime(_text(item, "pubDate"))
            except (TypeError, ValueError):
                published = None
        entries.append((_text(item, "title"), _text(item, "link"), published, _text(item, "description")))
    for entry in root.iter(f"{ATOM}entry"):                          # Atom
        link = ""
        for el in entry.findall(f"{ATOM}link"):
            if el.get("rel", "alternate") == "alternate":
                link = el.get("href", "")
                break
        stamp = _text(entry, f"{ATOM}published") or _text(entry, f"{ATOM}updated")
        published = None
        if stamp:
            try:
                published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                published = None
        summary = _text(entry, f"{ATOM}summary") or _text(entry, f"{ATOM}content")
        entries.append((_text(entry, f"{ATOM}title"), link, published, summary))
    if root.tag not in ("rss", f"{ATOM}feed", "rdf", "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF") \
            and not entries:
        raise WatchError("not an RSS or Atom feed")
    return entries


def matches(text, all_words=(), any_words=()):
    text = text.lower()
    return all(w.lower() in text for w in all_words) and \
        (not any_words or any(w.lower() in text for w in any_words))


def _feed_hits(fetch, src, since, label):
    hits = []
    for title, link, published, summary in parse_feed(fetch(src["url"])):
        # Links come from someone else's feed: keep only ordinary web links.
        if not link.lower().startswith(("https://", "http://")) or not matches(f"{title} {_strip_tags(summary)}", src.get("all") or (), src.get("any") or ()):
            continue
        if since and published is not None:
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published.date() < since:
                continue
        hits.append(Hit(f"feed:{link}", link, title or link, label,
                        published.date().isoformat() if published else ""))
    return hits


def _strip_tags(html):
    return re.sub(r"<[^>]+>", " ", html or "")
