"""Minimal read-only GitHub REST client (stdlib only)."""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(RuntimeError):
    status = None


def resolve_token():
    """SCOUT_GITHUB_TOKEN, GITHUB_TOKEN or GH_TOKEN, else `gh auth token`."""
    for name in ("SCOUT_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


class GitHub:
    """Reads public repos. The only writes are to the board issue in your own scout repo."""

    def __init__(self, token=None, api=API, sleep=time.sleep, search_interval=2.2):
        self.token = token
        self.api = api.rstrip("/")
        self._sleep = sleep
        self._search_interval = search_interval
        self._last_search = 0.0

    def _get(self, url):
        return self._request(url)

    def _request(self, url, method="GET", payload=None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "oss-scout",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return (json.loads(raw) if raw else None), resp.headers.get("Link")
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", "replace")
                wait = _retry_wait(err, body)
                if wait is not None and attempt < 2:
                    self._sleep(wait)
                    continue
                short_url = url.split("?")[0]
                message = " ".join(body.split())[:150]
                error = GitHubError(f"HTTP {err.code} for {short_url}: {message}")
                error.status = err.code
                raise error from None
            except urllib.error.URLError as err:
                if attempt < 2:
                    self._sleep(5)
                    continue
                raise GitHubError(f"network error for {url}: {err.reason}") from None
        raise GitHubError(f"giving up on {url}")

    def _pages(self, path, params=None, max_pages=5):
        url = f"{self.api}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for _ in range(max_pages):
            data, link = self._get(url)
            yield data
            match = _NEXT_RE.search(link or "")
            if not match:
                return
            url = match.group(1)

    def open_items(self, repo, label, limit=60):
        """Open items carrying `label`, newest activity first, split into
        (unassigned issues, pull requests). One listing serves both, which keeps
        the run off the search API and its strict secondary rate limits."""
        params = {"labels": label, "state": "open", "sort": "updated",
                  "direction": "desc", "per_page": 100}
        issues, prs = [], []
        for page in self._pages(f"/repos/{repo}/issues", params, max_pages=3):
            for item in page:
                if "pull_request" in item:
                    prs.append(item)
                elif not item.get("assignees") and len(issues) < limit:
                    issues.append(item)
        return issues, prs

    def search_total(self, query, page=1):
        """One page of issue/PR search: (total_count, items)."""
        gap = self._search_interval - (time.monotonic() - self._last_search)
        if gap > 0:
            self._sleep(gap)
        self._last_search = time.monotonic()
        params = urllib.parse.urlencode({"q": query, "per_page": 100, "page": page})
        data, _ = self._get(f"{self.api}/search/issues?{params}")
        return data.get("total_count", 0), data.get("items", [])

    def issue_comments(self, repo, number):
        comments = []
        for page in self._pages(f"/repos/{repo}/issues/{number}/comments", {"per_page": 100}, max_pages=10):
            comments.extend(page)
        return comments

    def all_pr_reviews(self, repo, number):
        reviews = []
        for page in self._pages(f"/repos/{repo}/pulls/{number}/reviews", {"per_page": 100}, max_pages=10):
            reviews.extend(page)
        return reviews

    def pull(self, repo, number):
        data, _ = self._get(f"{self.api}/repos/{repo}/pulls/{number}")
        return data

    def user(self, login):
        data, _ = self._get(f"{self.api}/users/{login}")
        return data

    def pr_reviews(self, repo, number):
        data, _ = self._get(f"{self.api}/repos/{repo}/pulls/{number}/reviews?per_page=100")
        return data

    # ---- writes: used only for the board issue in your own scout repo ----

    def find_open_issue(self, repo, label):
        data, _ = self._get(f"{self.api}/repos/{repo}/issues?labels={urllib.parse.quote(label)}"
                            "&state=open&per_page=10")
        issues = [i for i in data if "pull_request" not in i]
        return issues[0] if issues else None

    def ensure_label(self, repo, name, color, description):
        try:
            self._request(f"{self.api}/repos/{repo}/labels", "POST",
                          {"name": name, "color": color, "description": description})
        except GitHubError as err:
            if getattr(err, "status", None) != 422:  # 422 = already exists
                raise

    def create_issue(self, repo, title, body, labels):
        data, _ = self._request(f"{self.api}/repos/{repo}/issues", "POST",
                                {"title": title, "body": body, "labels": labels})
        return data

    def update_issue_body(self, repo, number, body):
        data, _ = self._request(f"{self.api}/repos/{repo}/issues/{number}", "PATCH", {"body": body})
        return data

    def add_comment(self, repo, number, body):
        data, _ = self._request(f"{self.api}/repos/{repo}/issues/{number}/comments", "POST", {"body": body})
        return data

    def timeline(self, repo, number, max_pages=5):
        events = []
        for page in self._pages(f"/repos/{repo}/issues/{number}/timeline", {"per_page": 100}, max_pages):
            events.extend(page)
        return events

    def search_issues(self, query, limit=100):
        """Issue/PR search. Paced to stay under the 30 requests/minute search limit."""
        gap = self._search_interval - (time.monotonic() - self._last_search)
        if gap > 0:
            self._sleep(gap)
        self._last_search = time.monotonic()
        items = []
        for page in self._pages("/search/issues", {"q": query, "per_page": 100}, max_pages=2):
            items.extend(page.get("items", []))
            if len(items) >= limit:
                break
        return items[:limit]


def _retry_wait(err, body=""):
    if err.code not in (403, 429):
        return None
    retry_after = err.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), 120)
    if "secondary rate limit" in body.lower():
        return 60
    if err.headers.get("X-RateLimit-Remaining") == "0":
        reset = err.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            wait = int(reset) - int(time.time()) + 1
            return wait if 0 < wait <= 120 else None
    return None
