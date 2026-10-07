"""Publications and peer reviews from a public ORCID record, with citation counts and venue
names from OpenAlex. Both APIs are free and need no key.

Only the `works` and `peer-reviews` sections of ORCID are read. Employment, education and
other personal sections are never fetched.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

ORCID_API = "https://pub.orcid.org/v3.0"
OPENALEX_API = "https://api.openalex.org"
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
KIND_LABELS = {"journal-article": "Journal article", "conference-paper": "Conference paper",
               "book-chapter": "Book chapter", "preprint": "Preprint", "book": "Book",
               "report": "Report", "dissertation-thesis": "Thesis"}


class ScholarError(RuntimeError):
    pass


def http_json(url, accept="application/json"):
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": "oss-scout (https://github.com/Abhimanyu9988/oss-scout)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise ScholarError(f"HTTP {err.code} for {url.split('?')[0]}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        raise ScholarError(f"couldn't read {url.split('?')[0]}: {err}") from None


def _v(node, *path):
    """Safe nested lookup that also unwraps ORCID's {"value": ...} wrappers."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _date(node):
    year, month, day = _v(node, "year"), _v(node, "month"), _v(node, "day")
    if not year:
        return "", ""
    label = f"{year}-{month}" if month else str(year)
    return f"{year}-{month or '01'}-{day or '01'}", label


def _doi(summary):
    for ext in _v(summary, "external-ids", "external-id") or []:
        if str(_v(ext, "external-id-type")).lower() == "doi" and _v(ext, "external-id-value"):
            if str(_v(ext, "external-id-relationship") or "self").lower() == "self":
                doi = str(_v(ext, "external-id-value")).strip()
                return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I).lower()
    return ""


def works(orcid, fetch):
    data = fetch(f"{ORCID_API}/{orcid}/works") or {}
    out = []
    for group in data.get("group", []) or []:
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        s = summaries[0]
        title = _v(s, "title", "title")
        if not title:
            continue
        when, label = _date(s.get("publication-date"))
        doi = _doi(s)
        url = f"https://doi.org/{doi}" if doi else (_v(s, "url") or f"https://orcid.org/{orcid}")
        out.append({"type": "paper", "url": url, "title": str(title).strip(), "date": when, "date_label": label,
                    "kind": _v(s, "type") or "other", "venue": _v(s, "journal-title") or "", "doi": doi,
                    "repo": "", "number": 0, "component": "general"})
    return out


def peer_reviews(orcid, fetch):
    data = fetch(f"{ORCID_API}/{orcid}/peer-reviews") or {}
    out = []
    for group in data.get("group", []) or []:
        for sub in group.get("peer-review-group", []) or []:
            for s in sub.get("peer-review-summary", []) or []:
                when, label = _date(s.get("completion-date"))
                put = s.get("put-code")
                out.append({"type": "peer_review", "url": _v(s, "review-url") or f"https://orcid.org/{orcid}#review-{put}",
                            "date": when, "date_label": label, "group_id": s.get("review-group-id") or "",
                            "org": _v(s, "convening-organization", "name") or "",
                            "role": s.get("reviewer-role") or "reviewer",
                            "repo": "", "number": 0, "component": "general"})
    return out


def _openalex(path, email):
    url = f"{OPENALEX_API}/{path}"
    if email:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"mailto": email})
    return url


def enrich(papers, reviews, fetch, email=""):
    """Citation counts and venue names for papers; venue names for peer reviews."""
    errors = []
    for p in papers:
        if not p["doi"]:
            continue
        try:
            w = fetch(_openalex(f"works/doi:{urllib.parse.quote(p['doi'], safe='/')}", email))
        except ScholarError as err:
            errors.append(str(err))
            continue
        if w:
            p["citations"] = int(w.get("cited_by_count") or 0)
            p["venue"] = p["venue"] or (_v(w, "primary_location", "source", "display_name") or "")
    names = {}
    for r in reviews:
        gid = r["group_id"]
        if gid.lower().startswith("issn:") and gid not in names:
            try:
                src = fetch(_openalex(f"sources/issn:{gid.split(':', 1)[1]}", email))
            except ScholarError as err:
                errors.append(str(err))
                src = None
            names[gid] = (src or {}).get("display_name", "")
        r["venue"] = names.get(gid) or r["org"] or gid or "Unspecified venue"
        r["title"] = f"Peer review for {r['venue']}"
    return errors


def collect(cfg, fetch=http_json):
    """Returns (records, errors). Records are empty when no ORCID iD is configured."""
    orcid = (cfg.get("orcid") or "").strip()
    if not orcid:
        return [], []
    if not ORCID_RE.match(orcid):
        return [], [f"'{orcid}' doesn't look like an ORCID iD (0000-0000-0000-0000)"]
    try:
        papers = works(orcid, fetch)
        reviews = peer_reviews(orcid, fetch)
    except ScholarError as err:
        return [], [f"ORCID: {err}"]
    errors = enrich(papers, reviews, fetch, cfg.get("openalex_email", ""))
    for r in papers + reviews:
        r.pop("group_id", None)
    return papers + reviews, [f"OpenAlex: {e}" for e in errors]


def kind_label(rec):
    return KIND_LABELS.get(rec.get("kind"), "Publication")
