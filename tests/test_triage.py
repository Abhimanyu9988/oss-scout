"""The triage queue (every new issue in your areas), waiting-on-others, and claim detection."""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import REPO, FakeGitHub, comment  # noqa: E402
from scout import board, slack  # noqa: E402
from scout.classify import CLAIM_RE  # noqa: E402
from scout.digest import build_digest, new_issues, waiting_on_others  # noqa: E402
from scout.github import GitHub  # noqa: E402

NOW = datetime(2026, 10, 12, 7, 0, tzinfo=timezone.utc)
USER = "Abhimanyu9988"
CORE = "open-telemetry/opentelemetry-collector"
SEMCONV = "open-telemetry/semantic-conventions"


def new(n, title, labels=(), author="reporter", created="2026-10-10T09:00:00Z", repo=REPO, pr=False):
    item = {"number": n, "title": title, "html_url": f"https://github.com/{repo}/issues/{n}",
            "labels": [{"name": lbl} for lbl in labels], "created_at": created, "updated_at": created,
            "user": {"login": author}, "repository_url": f"https://api.github.com/repos/{repo}"}
    if pr:
        item["pull_request"] = {}
    return item


def note(who, when="2026-10-10T10:00:00Z", bot=False):
    return {"user": {"login": who, "type": "Bot" if bot else "User"}, "created_at": when, "body": "..."}


def cfg(**triage):
    base = {"github_user": USER,
            "repos": [{"repo": REPO, "labels": ["receiver/kubeletstats", "receiver/sqlserver"]}],
            "triage": {"days": 7, "keywords": ["kubelet", "k8s", "sqlserver"]}}
    base["triage"].update(triage)
    return base


def contrib_world(comments=None):
    hits = [
        new(1, "kubeletstats: missing memory metric", ["bug", "receiver/kubeletstats", "needs triage"]),
        new(2, "sqlserver: add procedure_definition event", ["enhancement", "receiver/sqlserver"],
            created="2026-10-11T09:00:00Z"),
        new(3, "Collector crashes on k8s node restart", ["bug"]),        # no component label
        new(4, "Jaeger receiver panics", ["bug", "receiver/jaeger"]),   # outside your areas
        new(5, "kubeletstats: my own issue", ["receiver/kubeletstats"], author=USER),
        new(6, "kubeletstats PR", ["receiver/kubeletstats"], pr=True),
    ]
    return FakeGitHub(searches=[(f"repo:{REPO} is:issue", hits)], comments=comments or {})


class ClaimTest(unittest.TestCase):
    def test_offers_to_review_or_look_are_not_claims(self):
        for text in ("Happy to help review once there's a PR", "I'll take a look tomorrow",
                     "I will open a separate issue for that", "I can take a look", "happy to help test it"):
            self.assertIsNone(CLAIM_RE.search(text), text)

    def test_real_claims_still_count(self):
        for text in ("I'd like to work on this", "Happy to help!", "I'll open a PR this week",
                     "I can take this", "I will send a fix", "happy to pick this up"):
            self.assertIsNotNone(CLAIM_RE.search(text), text)


class NewIssuesTest(unittest.TestCase):
    def test_finds_labelled_and_unlabelled_issues_in_your_areas_only(self):
        found, _ = new_issues(contrib_world(), cfg(), NOW, {}, [])
        self.assertEqual(sorted(k.split("#")[1] for k in found), ["1", "2", "3"])
        detail = found[f"{REPO}#3"][0].detail
        self.assertIn("no component label", detail)
        self.assertTrue(detail.startswith("no reply yet"))

    def test_reply_status_ignores_bots_and_the_reporter(self):
        comments = {
            1: [note("github-actions[bot]", bot=True), note("reporter")],        # still no reply
            2: [note("ebrdarSplunk"), note("sincejune")],
            3: [note("someone"), note(USER)],
        }
        found, cache = new_issues(contrib_world(comments), cfg(), NOW, {}, [])
        d = {k.split("#")[1]: v[0].detail for k, v in found.items()}
        self.assertIn("no reply yet · needs triage · by reporter, 1d ago", d["1"])
        self.assertIn("2 replies, last from sincejune", d["2"])
        self.assertIn("you replied", d["3"])
        self.assertTrue(found[f"{REPO}#1"][1])
        self.assertEqual(cache[f"{REPO}#2"]["repliers"], ["ebrdarSplunk", "sincejune"])

    def test_a_claimed_or_blocked_issue_still_shows(self):
        # The old board only listed *free* issues, so a new issue claimed in its first comment, or
        # labelled waiting-for-code-owners, never appeared anywhere. Triage lists it regardless.
        gh = contrib_world({1: [note("someone", ) | {"body": "I'd like to work on this"}]})
        gh.searches[0][1][0]["labels"].append({"name": "waiting-for-code-owners"})
        found, _ = new_issues(gh, cfg(), NOW, {}, [])
        self.assertIn(f"{REPO}#1", found)
        self.assertIn("1 reply, last from someone", found[f"{REPO}#1"][0].detail)

    def test_unchanged_issues_are_not_re_read(self):
        gh = contrib_world()
        _, cache = new_issues(gh, cfg(), NOW, {}, [])
        self.assertEqual(len(gh.timeline_calls), 3)
        gh2 = contrib_world()
        new_issues(gh2, cfg(), NOW, {"triage_cache": cache}, [])
        self.assertEqual(gh2.timeline_calls, [])

    def test_triage_only_repos_whole_or_by_keyword(self):
        gh = FakeGitHub(searches=[
            (f"repo:{CORE} is:issue", [new(10, "confmap: nil map", ["bug"], repo=CORE)]),
            (f"repo:{SEMCONV} is:issue", [new(20, "k8s.pod.memory.usage unit", repo=SEMCONV),
                                          new(21, "HTTP span names", repo=SEMCONV)]),
        ])
        c = cfg(repos=[{"repo": CORE}, {"repo": SEMCONV, "keywords": ["k8s", "pod"]}])
        found, _ = new_issues(gh, c, NOW, {}, [])
        self.assertEqual(sorted(found), [f"{CORE}#10", f"{SEMCONV}#20"])
        self.assertIn(f"repo:{CORE} is:issue is:open created:>=2026-10-05", gh.queries)

    def test_can_be_turned_off(self):
        gh = contrib_world()
        self.assertEqual(new_issues(gh, cfg(enabled=False), NOW, {}, []), ({}, {}))
        self.assertEqual(gh.queries, [])


class TriageDigestTest(unittest.TestCase):
    def test_added_quietly_then_announces_only_new_issues(self):
        c = cfg()
        old_state = {"last_run": "2026-10-11T07:00:00Z", "seen": {"free": []}}   # from before this feature
        digest, state = build_digest(contrib_world(), c, old_state, NOW)
        self.assertEqual(digest.triage, [])                                       # no flood on upgrade
        self.assertEqual(len(digest.board_triage), 3)
        self.assertEqual(digest.counts["new_issues"], 3)

        gh = contrib_world()
        gh.searches[0][1].append(new(7, "kubeletstats: new today", ["receiver/kubeletstats"],
                                     created="2026-10-12T06:00:00Z"))
        digest, _ = build_digest(gh, c, state, NOW)
        self.assertEqual([i.key for i in digest.triage], [f"{REPO}#7"])
        text = board.render_comment(digest, c, NOW)
        self.assertIn("### Newly opened in your areas (1)", text)
        self.assertIn("by reporter, today", text)
        self.assertIn("Newly opened in your areas", slack.to_plain_text(slack.build_payload(digest, "t", "d")))

    def test_board_section_lists_no_reply_first(self):
        gh = contrib_world({2: [note("ebrdarSplunk")]})
        digest, _ = build_digest(gh, cfg(), {}, NOW)
        keys = [i.key.split("#")[1] for i in digest.board_triage]
        self.assertEqual(keys, ["3", "1", "2"])            # 3 and 1 have no reply (3 is newer by number)
        text = board.render_board(digest, cfg(), NOW, "Abhimanyu9988/oss-scout")
        self.assertIn("## To triage", text)
        self.assertIn("### Opened in the last 7 days in your areas, 2 with no reply yet (3)", text)
        self.assertLess(text.index("## To triage"), text.index("## To pick up"))
        self.assertIn("unlabelled ones mentioning kubelet, k8s, sqlserver", text)


def thread(n, title, author="someone", created="2026-09-01T10:00:00Z", pr=False, draft=False, repo=REPO):
    item = {"number": n, "title": title, "html_url": f"https://github.com/{repo}/issues/{n}",
            "user": {"login": author}, "created_at": created, "updated_at": created,
            "repository_url": f"https://api.github.com/repos/{repo}"}
    if pr:
        item["pull_request"] = {}
        item["draft"] = draft
    return item


def review(who, state, when):
    return {"event": "reviewed", "user": {"login": who}, "state": state, "submitted_at": when}


class WaitingTest(unittest.TestCase):
    CFG = {"github_user": USER, "repos": [{"repo": REPO}], "discover": {"orgs": ["open-telemetry"]}}

    def run_waiting(self, threads, timelines, skip=()):
        gh = FakeGitHub(searches=[("involves:", threads)], timelines=timelines)
        return waiting_on_others(gh, self.CFG, NOW, [], skip=skip), gh

    def test_your_comment_with_no_reply(self):
        out, gh = self.run_waiting(
            [thread(49927, "kubeletstats via API server proxy")],
            {49927: [comment("TylerHelmuth", "How would you distribute it?", "2026-09-28T10:00:00Z"),
                     comment(USER, "Benchmark results: ...", "2026-10-02T18:00:00Z"),
                     comment("github-actions[bot]", "This issue is stale", "2026-10-11T00:00:00Z")]})
        age, item = out[f"{REPO}#49927"]
        self.assertEqual(age, 9)
        self.assertEqual(item.detail, "you commented 9d ago, no reply since: a short nudge is fine")
        self.assertIn("involves:Abhimanyu9988 is:open updated:>=2026-08-13", gh.queries[0])

    def test_someone_replied_or_too_recent(self):
        out, _ = self.run_waiting(
            [thread(1, "a"), thread(2, "b")],
            {1: [comment(USER, "x", "2026-09-20T10:00:00Z"), comment("maintainer", "y", "2026-09-25T10:00:00Z")],
             2: [comment(USER, "x", "2026-10-09T10:00:00Z")]})
        self.assertEqual(out, {})

    def test_your_own_issue_nobody_answered(self):
        out, _ = self.run_waiting([thread(416, "OTel in Practice talk", author=USER,
                                          created="2026-10-02T10:00:00Z", repo="open-telemetry/sig-end-user")],
                                  {416: []})
        self.assertIn("you opened it 9d ago", out["open-telemetry/sig-end-user#416"][1].detail)

    def test_pushed_fixes_after_review(self):
        out, _ = self.run_waiting(
            [thread(2456, "operator TA image defaults", author=USER, pr=True)],
            {2456: [review("Allex1", "CHANGES_REQUESTED", "2026-10-01T10:00:00Z"),
                    {"event": "committed", "committer": {"date": "2026-10-03T12:00:00Z"}}]})
        self.assertIn("you pushed changes 8d ago", out[f"{REPO}#2456"][1].detail)

    def test_your_approval_on_someone_elses_pr_is_not_waiting(self):
        out, _ = self.run_waiting([thread(50135, "xmlns fix", author="Srikar", pr=True)],
                                  {50135: [review(USER, "APPROVED", "2026-09-20T10:00:00Z")]})
        self.assertEqual(out, {})

    def test_skips_drafts_and_items_reported_elsewhere(self):
        out, _ = self.run_waiting(
            [thread(1, "draft", author=USER, pr=True, draft=True), thread(2, "claimed")],
            {1: [comment(USER, "x", "2026-09-01T10:00:00Z")], 2: [comment(USER, "x", "2026-09-01T10:00:00Z")]},
            skip={f"{REPO}#2"})
        self.assertEqual(out, {})

    def test_on_the_board_and_announced_once(self):
        gh = FakeGitHub(searches=[("involves:Abhimanyu9988 is:open updated", [thread(49927, "proxy")])],
                        timelines={49927: [comment(USER, "results", "2026-10-02T18:00:00Z")]})
        state = {"last_run": "2026-10-11T07:00:00Z", "seen": {"waiting": []}}
        digest, new_state = build_digest(gh, self.CFG, state, NOW)
        self.assertEqual([i.key for i in digest.board_waiting], [f"{REPO}#49927"])
        self.assertIn(f"{REPO}#49927", [i.key for i in digest.needs_you])
        text = board.render_board(digest, self.CFG, NOW)
        self.assertIn("### Waiting on others: you spoke last 7+ days ago (1)", text)
        again, _ = build_digest(gh, self.CFG, new_state, NOW)
        self.assertNotIn(f"{REPO}#49927", [i.key for i in again.needs_you])


class SearchPagingTest(unittest.TestCase):
    def test_reads_enough_pages_for_the_limit(self):
        gh = GitHub(sleep=lambda s: None, search_interval=0)
        asked = []

        def pages(path, params=None, max_pages=5):
            asked.append(max_pages)
            for _ in range(max_pages):
                yield {"items": [{"n": 1}] * 100}
        gh._pages = pages
        self.assertEqual(len(gh.search_issues("q", limit=300)), 300)
        self.assertEqual(len(gh.search_issues("q", limit=40)), 40)
        self.assertEqual(asked, [3, 1])


if __name__ == "__main__":
    unittest.main()


class TriageLinkedPrTest(unittest.TestCase):
    def _world(self, events_for_1):
        gh = contrib_world()
        gh.timelines[1] = events_for_1
        return gh

    def _pr_ref(self, n, state="open", author="souvik-biswas-dev", repo=REPO, merged=False):
        return {"event": "cross-referenced", "created_at": "2026-10-10T11:00:00Z",
                "source": {"issue": {"number": n, "state": state, "user": {"login": author},
                                     "repository": {"full_name": repo},
                                     "pull_request": {"merged_at": "2026-10-11T00:00:00Z" if merged else None}}}}

    def test_open_pr_is_named_and_not_counted_as_needing_a_reply(self):
        found, cache = new_issues(self._world([self._pr_ref(16168)]), cfg(), NOW, {}, [])
        item, no_reply, _ = found[f"{REPO}#1"]
        self.assertIn("no reply yet · PR #16168 open by souvik-biswas-dev", item.detail)
        self.assertFalse(no_reply)
        self.assertEqual(cache[f"{REPO}#1"]["prs"], [[f"{REPO}#16168", "souvik-biswas-dev"]])

    def test_closed_and_merged_prs_are_ignored(self):
        events = [self._pr_ref(10, state="closed"), self._pr_ref(11, state="closed", merged=True)]
        found, _ = new_issues(self._world(events), cfg(), NOW, {}, [])
        self.assertNotIn("PR", found[f"{REPO}#1"][0].detail)
        self.assertTrue(found[f"{REPO}#1"][1])

    def test_your_own_pr_and_a_pr_in_another_repo(self):
        found, _ = new_issues(self._world([self._pr_ref(5, author=USER)]), cfg(), NOW, {}, [])
        self.assertIn("your PR #5 open", found[f"{REPO}#1"][0].detail)
        found, _ = new_issues(self._world([self._pr_ref(1, author="stuart23", repo="stuart23/fork")]),
                              cfg(), NOW, {}, [])
        self.assertIn("PR stuart23/fork#1 open by stuart23", found[f"{REPO}#1"][0].detail)

    def test_pr_linked_by_hand_without_a_cross_reference(self):
        found, _ = new_issues(self._world([{"event": "connected", "created_at": "2026-10-10T11:00:00Z"}]),
                              cfg(), NOW, {}, [])
        self.assertIn("a PR is linked", found[f"{REPO}#1"][0].detail)
        unlinked = [{"event": "connected"}, {"event": "disconnected"}]
        found, _ = new_issues(self._world(unlinked), cfg(), NOW, {}, [])
        self.assertNotIn("PR", found[f"{REPO}#1"][0].detail)

    def test_old_cache_entries_without_pr_info_are_re_read_once(self):
        _, cache = new_issues(contrib_world(), cfg(), NOW, {}, [])
        for entry in cache.values():
            del entry["prs"]
        gh = contrib_world()
        _, cache2 = new_issues(gh, cfg(), NOW, {"triage_cache": cache}, [])
        self.assertEqual(len(gh.timeline_calls), 3)
        self.assertTrue(all("prs" in e for e in cache2.values()))
