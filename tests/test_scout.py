import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import REPO, FakeGitHub, comment, issue, pr, xref  # noqa: E402
from scout import slack  # noqa: E402
from scout.classify import Candidate, classify  # noqa: E402
from scout.cli import main  # noqa: E402
from scout.digest import build_digest, describe_events  # noqa: E402

NOW = datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)
USER = "Abhimanyu9988"
CFG = {"github_user": USER, "quiet_days": 45, "stale_review_days": 7,
       "repos": [{"repo": REPO, "labels": ["receiver/kubeletstats", "receiver/k8scluster"]}]}


def cand(labels=()):
    return Candidate.from_issue(REPO, issue(1, "t", list(labels)), "receiver/kubeletstats")


class ClassifyTest(unittest.TestCase):
    def run_classify(self, timeline, labels=()):
        return classify(cand(labels), timeline, user=USER, now=NOW)

    def test_free(self):
        self.assertEqual(self.run_classify([]).bucket, "free")

    def test_open_pr_is_taken_and_cross_repo_ref_is_labelled(self):
        c = self.run_classify([xref(4158, "open", repo="open-telemetry/semantic-conventions")])
        self.assertEqual(c.bucket, "taken")
        self.assertIn("open-telemetry/semantic-conventions#4158", c.reason)

    def test_manually_linked_pr_is_taken(self):
        self.assertEqual(self.run_classify([{"event": "connected"}]).bucket, "taken")

    def test_old_claim_is_quiet(self):
        c = self.run_classify([comment("odubajDT", "I'd like to work on this", "2026-08-20T00:00:00Z")])
        self.assertEqual(c.bucket, "quiet")
        self.assertIn("odubajDT 48d", c.reason)

    def test_recent_claim_is_taken(self):
        c = self.run_classify([comment("x", "Happy to pick this up!", "2026-10-01T00:00:00Z")])
        self.assertEqual(c.bucket, "taken")

    def test_own_claim_is_mine(self):
        c = self.run_classify([comment(USER, "I can take this", "2026-09-01T00:00:00Z")])
        self.assertEqual(c.bucket, "mine")

    def test_bot_comments_are_ignored(self):
        c = self.run_classify([comment("github-actions[bot]", "I'd like to work on this", "2026-01-01T00:00:00Z")])
        self.assertEqual(c.bucket, "free")

    def test_blocking_label(self):
        self.assertEqual(self.run_classify([], labels=["waiting-for-code-owners"]).bucket, "blocked")

    def test_claim_wins_over_blocking_label(self):
        c = self.run_classify([comment("francois07", "I would like to work on it", "2026-08-10T00:00:00Z")],
                              labels=["discussion needed"])
        self.assertEqual(c.bucket, "quiet")

    def test_merged_pr_note(self):
        c = self.run_classify([xref(46172, "closed", merged=True)])
        self.assertEqual(c.bucket, "free")
        self.assertIn("#46172", c.reason)


class DescribeEventsTest(unittest.TestCase):
    def test_merge_collapses_close_and_skips_own_and_old_events(self):
        since = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
        events = [
            {"event": "reviewed", "user": {"login": "TylerHelmuth"}, "state": "APPROVED",
             "submitted_at": "2026-10-06T20:00:00Z"},
            {"event": "merged", "actor": {"login": "TylerHelmuth"}, "created_at": "2026-10-06T20:01:00Z"},
            {"event": "closed", "actor": {"login": "TylerHelmuth"}, "created_at": "2026-10-06T20:01:00Z"},
            comment(USER, "thanks!", "2026-10-06T21:00:00Z"),
            comment("otelbot", "Thank you for your contribution", "2026-10-06T20:02:00Z"),
            comment("krisztianfekete", "old", "2026-10-05T10:00:00Z"),
        ]
        self.assertEqual(describe_events(events, USER, since),
                         ["TylerHelmuth approved", "merged by TylerHelmuth"])


def world():
    issues = {
        "receiver/kubeletstats": [issue(47988, "Excessive float64 precision", ["receiver/kubeletstats"]),
                                  issue(40444, "Memory limit utilization", ["receiver/kubeletstats"])],
        "receiver/k8scluster": [issue(51856, "migrate semconv", ["receiver/k8scluster", "needs triage"],
                                      updated="2026-10-07T06:00:00Z"),
                                issue(47988, "duplicate under a second label", ["receiver/k8scluster"])],
    }
    timelines = {
        47988: [comment("odubajDT", "I'd like to work on this", "2026-08-20T00:00:00Z")],
        40444: [xref(4158, "open", repo="open-telemetry/semantic-conventions")],
        51657: [{"event": "merged", "actor": {"login": "TylerHelmuth"}, "created_at": "2026-10-06T20:01:00Z"}],
    }
    searches = [
        ("review-requested:", [pr(51999, "Fix something", "contributor")]),
        ("involves:", [pr(51657, "Fix flaky cache sync tests", USER)]),
    ]
    prs = {
        "receiver/k8scluster": [
            pr(51224, "sidecar metrics", "phantomnat", labels=["first-time contributor"]),
            pr(51300, "already reviewed", "a"),
            pr(51400, "a draft", "b", draft=True),
            pr(51500, "too new", "c", created="2026-10-05T00:00:00Z"),
            pr(51600, "my own PR", USER),
            pr(51700, "only the author commented", "d"),
        ],
        "receiver/kubeletstats": [pr(51224, "sidecar metrics", "phantomnat", labels=["first-time contributor"])],
    }
    reviews = {51300: [{"user": {"login": "TylerHelmuth"}}], 51700: [{"user": {"login": "d"}}]}
    return FakeGitHub(issues, timelines, searches, prs, reviews)


class DigestTest(unittest.TestCase):
    def test_first_run_is_a_baseline(self):
        gh = world()
        digest, state = build_digest(gh, CFG, {}, NOW)
        self.assertTrue(digest.baseline)
        self.assertEqual(digest.counts, {"quiet": 1, "taken": 1, "free": 1})
        self.assertEqual([i.key for i in digest.to_pick], [f"{REPO}#51856"])
        self.assertEqual([i.key for i in digest.needs_you], [f"{REPO}#51999"])
        self.assertEqual(digest.review_queue, [])
        self.assertFalse(any("involves:" in q for q in gh.queries))
        self.assertEqual(state["seen"]["quiet"], [f"{REPO}#47988"])
        self.assertEqual(state["seen"]["review_queue"], [f"{REPO}#51224", f"{REPO}#51700"])
        self.assertEqual(sorted(gh.review_calls), [51224, 51300, 51700])
        self.assertFalse(any("review:none" in q for q in gh.queries))
        self.assertEqual(len(gh.queries), 1)  # only the review-requested search on a first run

    def test_reported_prs_are_not_rechecked(self):
        _, state = build_digest(world(), CFG, {}, NOW)
        state["last_run"] = "2026-10-06T07:00:00Z"
        gh = world()
        build_digest(gh, CFG, state, NOW)
        self.assertEqual(gh.review_calls, [51300])

    def test_second_run_reports_only_changes(self):
        _, state = build_digest(world(), CFG, {}, NOW)
        state["last_run"] = "2026-10-06T07:00:00Z"
        state["seen"]["free"] = []            # pretend #51856 appeared since yesterday
        state["seen"]["review_queue"] = []    # and #51224 just crossed 7 days
        digest, _ = build_digest(world(), CFG, state, NOW)
        self.assertFalse(digest.baseline)
        self.assertEqual([i.key for i in digest.to_pick], [f"{REPO}#51856"])
        self.assertEqual([(i.key, i.detail) for i in digest.needs_you],
                         [(f"{REPO}#51657", "merged by TylerHelmuth")])
        self.assertEqual([i.key.split("#")[1] for i in digest.review_queue], ["51224", "51700"])
        self.assertIn("first-time contributor", digest.review_queue[0].detail)

    def test_nothing_new_is_empty(self):
        _, state = build_digest(world(), CFG, {}, NOW)
        state["last_run"] = "2026-10-07T06:59:00Z"
        digest, _ = build_digest(world(), CFG, state, NOW)
        self.assertTrue(digest.is_empty())


class TagTest(unittest.TestCase):
    def test_component_tag_only_when_title_lacks_it(self):
        from scout.digest import _cand_item
        a = Candidate.from_issue(REPO, issue(1, "[receiver/k8scluster] migrate semconv", []), "receiver/k8scluster")
        b = Candidate.from_issue(REPO, issue(2, "Expose pod owner", []), "processor/k8sattributes")
        a.reason = b.reason = ""
        self.assertEqual(_cand_item(a, "free").detail, "free")
        self.assertEqual(_cand_item(b, "free").detail, "[processor/k8sattributes] free")


class SlackTest(unittest.TestCase):
    def test_payload_escapes_and_links(self):
        gh = world()
        gh.issues_by_label["receiver/k8scluster"][0]["title"] = "a <b> & c"
        digest, _ = build_digest(gh, CFG, {}, NOW)
        payload = slack.build_payload(digest, "Contrib scout", "Wed 07 Oct")
        text = json.dumps(payload)
        self.assertIn("a &lt;b&gt; &amp; c", text)
        self.assertIn(f"<https://github.com/{REPO}/issues/51856|#51856>", text)
        self.assertLessEqual(max(len(b["text"]["text"]) for b in payload["blocks"] if b["type"] == "section"), 3000)


class CliTest(unittest.TestCase):
    def test_dry_run_then_quiet_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "scout.config.json")
            state_path = os.path.join(tmp, "state", "state.json")
            with open(cfg_path, "w") as fh:
                json.dump(CFG, fh)
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["--config", cfg_path, "digest", "--dry-run", "--state", state_path], gh=world(), now=NOW)
            self.assertEqual(rc, 0)
            self.assertIn("Watching from today", out.getvalue())
            self.assertTrue(os.path.exists(state_path))

            out = io.StringIO()
            with redirect_stdout(out):
                main(["--config", cfg_path, "digest", "--dry-run", "--state", state_path], gh=world(), now=NOW)
            self.assertIn("Nothing new today", out.getvalue())

    def test_total_failure_does_not_post_or_save(self):
        from scout.github import GitHubError

        class Broken(FakeGitHub):
            def open_items(self, *a, **k):
                raise GitHubError("HTTP 401")

            def search_issues(self, *a, **k):
                raise GitHubError("HTTP 401")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "c.json")
            state_path = os.path.join(tmp, "s.json")
            with open(cfg_path, "w") as fh:
                json.dump(CFG, fh)
            with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = main(["--config", cfg_path, "digest", "--dry-run", "--state", state_path], gh=Broken(), now=NOW)
            self.assertEqual(rc, 3)
            self.assertFalse(os.path.exists(state_path))

    def test_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "c.json")
            with open(cfg_path, "w") as fh:
                json.dump(CFG, fh)
            out = io.StringIO()
            with redirect_stdout(out):
                main(["--config", cfg_path, "report"], gh=world(), now=NOW)
            self.assertIn("## Claimed but quiet (1)", out.getvalue())
            self.assertIn("semantic-conventions#4158", out.getvalue())


if __name__ == "__main__":
    unittest.main()
