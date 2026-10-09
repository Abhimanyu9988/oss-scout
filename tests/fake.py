"""In-memory stand-in for scout.github.GitHub."""

REPO = "open-telemetry/opentelemetry-collector-contrib"
API_REPO = f"https://api.github.com/repos/{REPO}"


def issue(n, title, labels, updated="2026-10-01T10:00:00Z", author="someone"):
    return {"number": n, "title": title, "html_url": f"https://github.com/{REPO}/issues/{n}",
            "labels": [{"name": l} for l in labels], "updated_at": updated,
            "user": {"login": author}, "repository_url": API_REPO}


def pr(n, title, author, created="2026-09-20T10:00:00Z", labels=(), draft=False):
    return {"number": n, "title": title, "html_url": f"https://github.com/{REPO}/pull/{n}",
            "user": {"login": author}, "created_at": created, "repository_url": API_REPO,
            "labels": [{"name": l} for l in labels], "pull_request": {}, "draft": draft,
            "assignees": []}


def comment(who, body, when):
    return {"event": "commented", "actor": {"login": who}, "user": {"login": who},
            "body": body, "created_at": when}


def xref(n, state, repo=REPO, merged=False):
    return {"event": "cross-referenced", "created_at": "2026-09-01T00:00:00Z",
            "source": {"issue": {"number": n, "state": state,
                                 "repository": {"full_name": repo},
                                 "pull_request": {"merged_at": "2026-09-02T00:00:00Z" if merged else None}}}}


class FakeWriter:
    """Records writes to the board repo."""

    def __init__(self, existing=None, fail=False):
        self.existing = existing
        self.fail = fail
        self.calls = []

    def find_open_issue(self, repo, label):
        self.calls.append(("find", repo, label))
        if self.fail:
            from scout.github import GitHubError
            raise GitHubError("HTTP 403 for board: Resource not accessible by integration")
        return self.existing

    def ensure_label(self, repo, name, color, description):
        self.calls.append(("label", name))

    def create_issue(self, repo, title, body, labels):
        self.calls.append(("create", title, body, labels))
        return {"number": 1, "html_url": f"https://github.com/{repo}/issues/1"}

    def update_issue_body(self, repo, number, body):
        self.calls.append(("update", number, body))

    def add_comment(self, repo, number, body):
        self.calls.append(("comment", number, body))

    def close_issue(self, repo, number):
        self.calls.append(("close", number))


class FakeGitHub:
    def __init__(self, issues_by_label=None, timelines=None, searches=None, prs_by_label=None, reviews=None):
        self.issues_by_label = issues_by_label or {}
        self.prs_by_label = prs_by_label or {}
        self.timelines = timelines or {}
        self.reviews = reviews or {}
        self.searches = searches or []   # list of (substring, results)
        self.queries = []
        self.review_calls = []

    def open_items(self, repo, label, limit=60):
        return self.issues_by_label.get(label, [])[:limit], self.prs_by_label.get(label, [])

    def pr_reviews(self, repo, number):
        self.review_calls.append(number)
        return self.reviews.get(number, [])

    def timeline(self, repo, number, max_pages=5):
        return self.timelines.get(number, [])

    def search_issues(self, query, limit=100, sort=None):
        self.queries.append(query)
        for needle, results in self.searches:
            if needle in query:
                return results[:limit]
        return []
