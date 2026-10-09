import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scout import contrib_log  # noqa: E402
from scout.cli import main  # noqa: E402
from scout.github import GitHubError  # noqa: E402

USER = "Abhimanyu9988"
C = "open-telemetry/opentelemetry-collector-contrib"
API = f"https://api.github.com/repos/{C}"
TODAY = date(2026, 10, 7)
CFG = contrib_log.load_log_config.__globals__["DEFAULTS"].copy()
CFG.update(github_user=USER, orgs=["open-telemetry"], component_label_patterns=contrib_log.DEFAULT_COMPONENT_PATTERNS)


def item(n, title, labels=(), author=USER, created="2026-09-20T10:00:00Z", state="closed", pr=False, merged=None):
    it = {"number": n, "title": title, "html_url": f"https://github.com/{C}/{'pull' if pr else 'issues'}/{n}",
          "repository_url": API, "labels": [{"name": l} for l in labels], "user": {"login": author},
          "created_at": created, "state": state}
    if pr:
        it["pull_request"] = {"merged_at": merged}
    return it


class FakeGH:
    def __init__(self):
        self.searches = {
            "author:": [
                item(51657, "Fix flaky cache sync tests", ["internal/k8sinventory"], pr=True, merged="2026-10-06T20:00:00Z",
                     created="2026-09-30T10:00:00Z"),
                item(51138, "Document TLS | handshake <issue>", ["receiver/sqlserver"], pr=True, merged="2026-09-21T10:00:00Z"),
                item(51700, "Open PR", ["receiver/kubeletstats"], pr=True, state="open"),
                item(51800, "An issue I opened", [], state="open"),
            ],
            "reviewed-by:": [item(50135, "Emit well-formed plans", ["receiver/sqlserver"], author="Srikar", pr=True, state="open")],
            "commenter:": [
                item(50774, "TLS handshake failure", ["receiver/sqlserver"], author="jy"),
                item(51657, "Fix flaky cache sync tests", ["internal/k8sinventory"], pr=True, merged="2026-10-06T20:00:00Z"),
            ],
        }
        self.reviews = {50135: [
            {"user": {"login": USER}, "state": "APPROVED", "html_url": f"https://github.com/{C}/pull/50135#pullrequestreview-1",
             "submitted_at": "2026-09-25T10:00:00Z"},
            {"user": {"login": USER}, "state": "PENDING", "html_url": "x", "submitted_at": None},
            {"user": {"login": "someone"}, "state": "APPROVED", "html_url": "y", "submitted_at": "2026-09-26T10:00:00Z"},
        ]}
        self.comments = {
            50774: [{"user": {"login": USER}, "html_url": f"https://github.com/{C}/issues/50774#issuecomment-1",
                     "created_at": "2026-09-18T10:00:00Z"},
                    {"user": {"login": "jy"}, "html_url": "z", "created_at": "2026-09-18T11:00:00Z"}],
            51657: [{"user": {"login": USER}, "html_url": f"https://github.com/{C}/pull/51657#issuecomment-2",
                     "created_at": "2026-10-06T09:00:00Z"}],
        }
        self.queries = []
        self.fail = False

    def search_total(self, query, page=1):
        self.queries.append(query)
        if self.fail:
            raise GitHubError("HTTP 403 rate limited")
        for key, items in self.searches.items():
            if key in query and not (key == "author:" and ("reviewed-by" in query or "commenter" in query)):
                chunk = items[(page - 1) * 100: page * 100]
                return len(items), chunk
        return 0, []

    def user(self, login):
        return {"created_at": "2014-03-01T00:00:00Z"}

    pulls = {
        51657: {"additions": 29, "deletions": 2, "changed_files": 1, "comments": 8, "review_comments": 3,
                "merged_by": {"login": "TylerHelmuth"}, "body": "Fixes #50433\n\nAlso closes open-telemetry/semantic-conventions#4133"},
        51138: {"additions": 42, "deletions": 0, "changed_files": 1, "comments": 9, "review_comments": 2,
                "merged_by": {"login": "songy23"}, "body": "Docs only."},
    }
    pull_fail = False

    def pull(self, repo, n):
        if self.pull_fail:
            raise GitHubError("HTTP 502")
        return self.pulls[n]

    def all_pr_reviews(self, repo, n):
        if n == 51657:
            return [{"user": {"login": "TylerHelmuth"}, "state": "APPROVED", "body": "Great fix, thanks!",
                     "html_url": f"https://github.com/{C}/pull/51657#pullrequestreview-9", "submitted_at": "2026-10-06T19:00:00Z"},
                    {"user": {"login": "krisztianfekete"}, "state": "APPROVED", "body": "",
                     "html_url": "r2", "submitted_at": "2026-10-06T18:00:00Z"}]
        return self.reviews.get(n, [])

    def issue_comments(self, repo, n):
        return self.comments.get(n, [])


class SearchAllTest(unittest.TestCase):
    def test_splits_ranges_that_hit_the_cap(self):
        class Big:
            def __init__(self):
                self.ranges = []

            def search_total(self, q, page=1):
                lo, hi = re.search(r"created:(\S+)\.\.(\S+)", q).groups()
                days = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
                self.ranges.append((lo, hi))
                total = days * 40          # 40 results a day: any slice over 25 days hits the cap
                return total, [{"n": i} for i in range(min(100, total))] if page == 1 else []

        gh, errors = Big(), []
        contrib_log.search_all(gh, "author:x", "created", date(2026, 1, 1), date(2026, 3, 31), errors)
        leaf_spans = [(date.fromisoformat(h) - date.fromisoformat(l)).days + 1 for l, h in gh.ranges]
        self.assertTrue(any(s <= 25 for s in leaf_spans))
        self.assertEqual(errors, [])
        covered = sorted(r for r in gh.ranges if (date.fromisoformat(r[1]) - date.fromisoformat(r[0])).days + 1 <= 25)
        self.assertEqual(covered[0][0], "2026-01-01")
        self.assertEqual(covered[-1][1], "2026-03-31")


class CollectTest(unittest.TestCase):
    def test_full_backfill(self):
        gh = FakeGH()
        records, errors = contrib_log.collect(gh, CFG, [], TODAY, full=True)
        self.assertEqual(errors, [])
        by_type = {}
        for r in records:
            by_type.setdefault(r["type"], []).append(r)
        self.assertEqual(sorted(r["state"] for r in by_type["pr"]), ["merged", "merged", "open"])
        self.assertEqual(len(by_type["issue"]), 1)
        self.assertEqual([r["url"] for r in by_type["review"]], [f"https://github.com/{C}/pull/50135#pullrequestreview-1"])
        self.assertEqual(by_type["review"][0]["review_state"], "approved")
        comments = {r["url"]: r for r in by_type["comment"]}
        self.assertFalse(comments[f"https://github.com/{C}/issues/50774#issuecomment-1"]["on_own_item"])
        self.assertTrue(comments[f"https://github.com/{C}/pull/51657#issuecomment-2"]["on_own_item"])
        self.assertTrue(all("created:2014-03-01..2026-10-07" in q for q in gh.queries))
        self.assertTrue(any("-author:Abhimanyu9988" in q and "reviewed-by:" in q for q in gh.queries))
        merged = next(r for r in by_type["pr"] if r["number"] == 51657)
        self.assertEqual((merged["component"], merged["merged"]), ("internal/k8sinventory", "2026-10-06"))

    def test_incremental_keeps_history_and_refreshes_threads(self):
        full, _ = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        old_only = {"url": "https://github.com/x/y/pull/1", "type": "pr", "repo": "x/y", "number": 1, "title": "old",
                    "date": "2025-01-01", "component": "general", "state": "merged"}
        gh = FakeGH()
        gh.searches["author:"] = []
        gh.comments[50774] = []          # I deleted that comment
        records, _ = contrib_log.collect(gh, CFG, full + [old_only], TODAY)
        urls = {r["url"] for r in records}
        self.assertIn(old_only["url"], urls)
        self.assertNotIn(f"https://github.com/{C}/issues/50774#issuecomment-1", urls)
        self.assertTrue(all("updated:2026-09-23..2026-10-07" in q for q in gh.queries))


class RenderTest(unittest.TestCase):
    def records(self, annotations=None):
        recs, _ = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        return contrib_log.apply_annotations(recs, annotations or {})

    def test_activity_block_keeps_to_recent_months(self):
        recs = self.records()
        now = contrib_log.render_section(recs, CFG, TODAY)
        self.assertNotIn("older item", now)
        later = contrib_log.render_section(recs, dict(CFG, readme_activity_months=1), date(2027, 6, 1))
        self.assertIn("<b>All activity</b>, last 1 month", later)
        self.assertRegex(later, r"_\d+ older items? kept in \[contributions.json\]")
        self.assertIn("**2 merged PRs**", later)          # headline and merged list keep everything

    def test_section(self):
        text = contrib_log.render_section(self.records(), CFG)
        self.assertTrue(text.startswith(contrib_log.START) and text.rstrip().endswith(contrib_log.END))
        self.assertIn("**2 merged PRs** · 1 open · **1 PR reviewed** · 1 comment on others' issues and PRs · 1 issue opened", text)
        self.assertIn("Active since Sep 2026 across 1 repository · components `internal/k8sinventory`, "
                      "`receiver/kubeletstats`, `receiver/sqlserver`", text)
        self.assertIn("### Merged", text)
        self.assertIn("### Recent activity", text)
        self.assertIn("<summary><b>All activity</b>", text)
        self.assertIn("Document TLS \\| handshake &lt;issue&gt;", text)
        self.assertNotIn("issuecomment-2", text)          # replies on my own PR are hidden by default
        self.assertNotIn("| Note |", text)
        self.assertIn("| 2026-09-25 | Review · approved |", text)

    def test_annotations_add_notes_but_tags_stay_out_by_default(self):
        url = f"https://github.com/{C}/pull/51657"
        text = contrib_log.render_section(self.records({url: {"note": "Unblocked CI", "tags": ["secret-tag"]}}), CFG)
        self.assertIn("| Note |", text)
        self.assertIn("Unblocked CI", text)
        self.assertNotIn("secret-tag", text)

    def test_replace_section_keeps_the_rest_of_the_readme(self):
        readme = "# Hi\n\nAbout me.\n\n<!-- contrib-log:start -->\nold\n<!-- contrib-log:end -->\n\nFooter\n"
        out = contrib_log.replace_section(readme, contrib_log.START + "\nnew\n" + contrib_log.END + "\n")
        self.assertEqual(out, "# Hi\n\nAbout me.\n\n<!-- contrib-log:start -->\nnew\n<!-- contrib-log:end -->\n\nFooter\n")
        self.assertIn("About me.\n\n<!-- contrib-log:start -->", contrib_log.replace_section("About me.\n", "<!-- contrib-log:start -->\nx\n<!-- contrib-log:end -->\n"))


class DetailsTest(unittest.TestCase):
    def test_merged_pr_details(self):
        recs, errors = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        self.assertEqual(errors, [])
        pr = next(r for r in recs if r["type"] == "pr" and r["number"] == 51657)
        self.assertEqual((pr["additions"], pr["deletions"], pr["files"], pr["discussion"]), (29, 2, 1, 11))
        self.assertEqual(pr["merged_by"], "TylerHelmuth")
        self.assertEqual(pr["approved_by"], ["TylerHelmuth", "krisztianfekete"])
        self.assertEqual(pr["fixes"], [f"https://github.com/{C}/issues/50433",
                                       "https://github.com/open-telemetry/semantic-conventions/issues/4133"])
        self.assertEqual([(q["by"], q["text"]) for q in pr["recognition"]], [("TylerHelmuth", "Great fix, thanks!")])
        open_pr = next(r for r in recs if r["number"] == 51700)
        self.assertNotIn("additions", open_pr)            # details only for merged PRs
        review = next(r for r in recs if r["type"] == "review")
        self.assertEqual(review["author"], "Srikar")

    def test_detail_failure_keeps_previous_details(self):
        first, _ = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        gh = FakeGH()
        gh.pull_fail = True
        recs, errors = contrib_log.collect(gh, CFG, first, TODAY)
        self.assertTrue(errors)
        self.assertEqual(next(r for r in recs if r["number"] == 51657 and r["type"] == "pr")["merged_by"], "TylerHelmuth")

    def test_merged_line_and_hidden_closed_prs(self):
        recs, _ = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        recs.append({"url": f"https://github.com/{C}/pull/51150", "type": "pr", "repo": C, "number": 51150,
                     "title": "Closed duplicate", "date": "2026-09-19", "component": "receiver/sqlserver", "state": "closed"})
        text = contrib_log.render_section(contrib_log.sort_records(recs), CFG)
        self.assertIn("- **[#51657 Fix flaky cache sync tests](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/51657)**"
                      " · `internal/k8sinventory` · merged 2026-10-06 by TylerHelmuth · approved by krisztianfekete"
                      " · +29 −2 in 1 file · fixes [#50433](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/50433), "
                      "[open-telemetry/semantic-conventions#4133](https://github.com/open-telemetry/semantic-conventions/issues/4133)", text)
        self.assertNotIn("51150", text)
        shown = dict(CFG, readme_hide_closed_prs=False)
        self.assertIn("51150", contrib_log.render_section(contrib_log.sort_records(recs), shown))

    def test_extras(self):
        notes = {"extras": [{"date": date(2026, 11, 24), "type": "talk", "title": "Kubelet stats | in practice",
                             "url": "https://example.org/talk", "note": "Community day", "tags": ["speaking"]},
                            {"date": "2026-10-01", "type": "meeting", "title": "SIG meeting demo"},
                            {"type": "talk"}]}
        extras = contrib_log.parse_extras(notes)
        self.assertEqual(len(extras), 2)
        self.assertEqual(extras[1]["url"], "extra:2026-10-01:sig-meeting-demo")
        recs, _ = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        text = contrib_log.render_section(contrib_log.sort_records(recs + extras), CFG)
        self.assertIn("### Beyond GitHub", text)
        self.assertIn("- 2026-11-24 · Talk · [Kubelet stats \\| in practice](https://example.org/talk) — Community day", text)
        self.assertIn("- 2026-10-01 · Meeting · SIG meeting demo", text)
        self.assertNotIn("speaking", text)
        self.assertEqual(contrib_log.summary_counts(recs + extras)["merged"], 2)


class FixesTest(unittest.TestCase):
    def test_plurals(self):
        self.assertEqual(contrib_log._plural(5, "repository"), "5 repositories")
        self.assertEqual(contrib_log._plural(1, "repository"), "1 repository")
        self.assertEqual(contrib_log._plural(2, "day"), "2 days")
        self.assertEqual(contrib_log._plural(3, "PR"), "3 PRs")
        self.assertEqual(contrib_log._plural(2, "discussion"), "2 discussions")

    def test_praise_uses_real_world_examples(self):
        genuine = ("Thanks for picking this up — this is a real documentation gap, and the failover-cluster note is "
                   "the kind of thing that costs people hours. A few things before I can approve.\n\n## Blocking\n**The...")
        self.assertTrue(contrib_log.praise(genuine).startswith("Thanks for picking this up — this is a real documentation gap"))
        self.assertNotIn("Blocking", contrib_log.praise(genuine))
        self.assertEqual(contrib_log.praise("Thanks, added a few comments!"), "")
        self.assertEqual(contrib_log.praise("This PR has been approved by the code-owner. Could someone from "
                                            "@open-telemetry/collector-contrib-approvers please take a look at it? Thanks!"), "")
        self.assertEqual(contrib_log.praise("Great work! This unblocks the release."), "Great work! This unblocks the release.")
        self.assertEqual(contrib_log.praise("> Great idea\n\nI disagree."), "")      # quoting someone else

    def test_review_rounds_collapse(self):
        p = f"https://github.com/{C}/pull/51231"
        rounds = [{"type": "review", "url": p + "#r1", "parent_url": p, "date": "2026-09-20", "review_state": "commented",
                   "repo": C, "number": 51231, "title": "t", "component": "receiver/sqlserver"},
                  {"type": "review", "url": p + "#r2", "parent_url": p, "date": "2026-09-21", "review_state": "approved",
                   "repo": C, "number": 51231, "title": "t", "component": "receiver/sqlserver"}]
        out = contrib_log.collapse_reviews(rounds)
        self.assertEqual(len(out), 1)
        self.assertEqual(contrib_log._type_label(out[0]), "Review · approved (2 rounds)")
        self.assertEqual(contrib_log.summary_counts(rounds)["reviews"], 1)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "README.md"), "w") as fh:
            fh.write("# Abhi\n\nHand-written intro.\n")
        self.cfg = dict(CFG)

    def test_idempotent_and_only_writes_on_change(self):
        changed, errors, _ = contrib_log.run(FakeGH(), self.cfg, TODAY, base_dir=self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(len(changed), 2)
        with open(os.path.join(self.dir, "README.md")) as fh:
            self.assertTrue(fh.read().startswith("# Abhi\n\nHand-written intro.\n\n<!-- contrib-log:start -->"))
        changed, _, _ = contrib_log.run(FakeGH(), self.cfg, TODAY, base_dir=self.dir)
        self.assertEqual(changed, [])
        changed, _, _ = contrib_log.run(FakeGH(), self.cfg, TODAY, full=True, base_dir=self.dir)
        self.assertEqual(changed, [])

    def test_failed_rebuild_keeps_existing_files(self):
        contrib_log.run(FakeGH(), self.cfg, TODAY, base_dir=self.dir)
        with open(os.path.join(self.dir, "contributions.json")) as fh:
            before = fh.read()
        gh = FakeGH()
        gh.fail = True
        changed, errors, _ = contrib_log.run(gh, self.cfg, TODAY, full=True, base_dir=self.dir)
        self.assertEqual(changed, [])
        self.assertTrue(errors)
        with open(os.path.join(self.dir, "contributions.json")) as fh:
            self.assertEqual(fh.read(), before)

    def test_json_annotations_file(self):
        with open(os.path.join(self.dir, "annotations.json"), "w") as fh:
            json.dump({f"https://github.com/{C}/pull/51138": "First merged contribution"}, fh)
        self.cfg["annotations_path"] = "annotations.json"
        _, _, records = contrib_log.run(FakeGH(), self.cfg, TODAY, base_dir=self.dir)
        self.assertEqual(next(r for r in records if r["number"] == 51138 and r["type"] == "pr")["note"],
                         "First merged contribution")

    def test_comment_only_yaml_annotations_need_no_pyyaml(self):
        with open(os.path.join(self.dir, "annotations.yaml"), "w") as fh:
            fh.write("# notes go here\n# https://github.com/...: why it mattered\n")
        self.assertEqual(contrib_log.load_annotations(os.path.join(self.dir, "annotations.yaml")), {})

    def test_cli(self):
        cfg_path = os.path.join(self.dir, "contrib-log.config.json")
        with open(cfg_path, "w") as fh:
            json.dump({"github_user": USER, "orgs": ["open-telemetry"],
                       "readme_path": os.path.join(self.dir, "README.md"),
                       "data_path": os.path.join(self.dir, "contributions.json")}, fh)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["log", "--log-config", cfg_path, "--dry-run"], gh=FakeGH())
        self.assertEqual(rc, 0)
        self.assertIn("2 merged PRs, 1 review, 1 comment on others' work, 1 issue opened", out.getvalue())
        self.assertIn("Would change", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "contributions.json")))


if __name__ == "__main__":
    unittest.main()
