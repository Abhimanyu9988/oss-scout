import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scout.github import GitHub, GitHubError  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def _send(self, code, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        Handler.calls.append((url.path, q, self.headers.get("Authorization")))
        base = f"http://127.0.0.1:{self.server.server_port}"
        if url.path == "/repos/o/r/issues":
            if q.get("page") == ["2"]:
                return self._send(200, [{"number": 3}, {"number": 4, "assignees": [{"login": "x"}]}])
            return self._send(200, [{"number": 1}, {"number": 2, "pull_request": {}}],
                              {"Link": f'<{base}/repos/o/r/issues?page=2>; rel="next"'})
        if url.path == "/repos/o/r/pulls/7/reviews":
            if not getattr(self.server, "secondary", False):
                self.server.secondary = True
                return self._send(403, {"message": "You have exceeded a secondary rate limit."})
            return self._send(200, [{"user": {"login": "rev"}}])
        if url.path == "/search/issues":
            return self._send(200, {"items": [{"number": 9}]})
        if url.path == "/repos/o/r/issues/5/timeline":
            if not getattr(self.server, "throttled", False):
                self.server.throttled = True
                return self._send(403, {"message": "rate limited"}, {"Retry-After": "1"})
            return self._send(200, [{"event": "commented"}])
        return self._send(404, {"message": "Not Found"})


class WriteHandler(Handler):
    writes = []

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_POST(self):
        payload = self._body()
        WriteHandler.writes.append(("POST", self.path, payload))
        if self.path == "/repos/me/scout/labels":
            return self._send(422, {"message": "Validation Failed", "errors": [{"code": "already_exists"}]})
        if self.path == "/repos/me/scout/issues":
            return self._send(201, {"number": 1, "html_url": "https://github.com/me/scout/issues/1"})
        if self.path == "/repos/me/scout/issues/1/comments":
            return self._send(201, {"id": 5})
        return self._send(404, {})

    def do_PATCH(self):
        WriteHandler.writes.append(("PATCH", self.path, self._body()))
        return self._send(200, {"number": 1})


class WriteTest(unittest.TestCase):
    def test_board_write_calls(self):
        server = HTTPServer(("127.0.0.1", 0), WriteHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            gh = GitHub("tok", api=f"http://127.0.0.1:{server.server_port}", sleep=lambda s: None)
            gh.ensure_label("me/scout", "oss-scout", "0e8a16", "d")       # 422 is swallowed
            issue = gh.create_issue("me/scout", "Contribution board", "body", ["oss-scout"])
            gh.update_issue_body("me/scout", 1, "new body")
            gh.add_comment("me/scout", 1, "hello")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(issue["number"], 1)
        self.assertEqual([(m, p) for m, p, _ in WriteHandler.writes], [
            ("POST", "/repos/me/scout/labels"), ("POST", "/repos/me/scout/issues"),
            ("PATCH", "/repos/me/scout/issues/1"), ("POST", "/repos/me/scout/issues/1/comments")])
        self.assertEqual(WriteHandler.writes[2][2], {"body": "new body"})


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.gh = GitHub("tok", api=f"http://127.0.0.1:{cls.server.server_port}",
                        sleep=lambda s: None, search_interval=0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_paginates_and_splits_issues_from_prs(self):
        issues, prs = self.gh.open_items("o/r", "x")
        self.assertEqual([i["number"] for i in issues], [1, 3])   # #4 is assigned
        self.assertEqual([p["number"] for p in prs], [2])
        path, query, auth = next(c for c in Handler.calls if c[0] == "/repos/o/r/issues" and "labels" in c[1])
        self.assertEqual(auth, "Bearer tok")

    def test_waits_on_secondary_rate_limit(self):
        waits = []
        gh = GitHub("tok", api=self.gh.api, sleep=waits.append, search_interval=0)
        self.assertEqual(gh.pr_reviews("o/r", 7), [{"user": {"login": "rev"}}])
        self.assertEqual(waits, [60])

    def test_error_message_is_short(self):
        with self.assertRaises(GitHubError) as ctx:
            self.gh.timeline("o/r", 404)
        self.assertNotIn("?", str(ctx.exception))
        self.assertLess(len(str(ctx.exception)), 250)

    def test_search(self):
        self.assertEqual(self.gh.search_issues("is:pr")[0]["number"], 9)

    def test_retries_after_rate_limit(self):
        self.assertEqual(self.gh.timeline("o/r", 5), [{"event": "commented"}])

    def test_404_raises(self):
        with self.assertRaises(GitHubError):
            self.gh.timeline("o/r", 404)


if __name__ == "__main__":
    unittest.main()
