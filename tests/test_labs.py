import base64
import json
import unittest

from scout import labs
from scout.board import render_labs
from scout.digest import Digest
from scout.github import GitHubError

CFG = {"github_user": "me", "repos": [{"repo": "open-telemetry/opentelemetry-collector-contrib",
                                        "labels": ["receiver/flinkmetrics"]}],
       "labs": {"repo": "me/site", "articles": "content/articles", "labs": "labs",
                "site": "https://example.com/articles/{slug}/"}}
ARTICLE = "Completed checkpoints were +53 in Flink's REST API and +58 in Splunk."
LOG = "checkpoints completed from 1 to 2: Splunk +58, Flink's REST API +53\nHERO_RESULT: fixed\n"


def b64(text):
    return base64.b64encode(text.encode()).decode()


class FakeGH:
    api = "https://api.github.com"

    def __init__(self, files, search=None):
        self.files, self.search, self.queries = files, search or [], []

    def _get(self, url):
        path = url.split("/contents/", 1)[1]
        if path in self.files:
            v = self.files[path]
            return (v if isinstance(v, list) else {"content": b64(v)}), None
        err = GitHubError(f"HTTP 404 for {path}")
        err.status = 404
        raise err

    def search_issues(self, query, limit=100, sort=None):
        self.queries.append(query)
        return self.search


def world(article=ARTICLE, log=LOG, sha="a1"):
    return {"content/articles": [{"name": "flink.md", "sha": sha}],
            "content/articles/flink.md": article,
            "labs/flink/README.md": "readme",
            "labs/flink/evidence": [{"name": "05-fix.log", "sha": "e1"}, {"name": "01-init.log", "sha": "e0"}],
            "labs/flink/evidence/05-fix.log": log}


class FakeJudge:
    def __init__(self, findings):
        self.findings, self.calls, self.prompts = findings, 0, []

    def ask_json(self, system, prompt, model=None, max_tokens=None):
        self.calls += 1
        self.prompts.append(prompt)
        return json.dumps({"findings": self.findings})


GOOD = {"kind": "bug", "repo": "open-telemetry/opentelemetry-collector-contrib", "label": "receiver/flinkmetrics",
        "title": "flink.job.checkpoint.count completed is higher than Flink's own count",
        "evidence": "Splunk +58, Flink's REST API +53", "why": "counts differ", "body": "Observed ..."}


class LabFindingsTest(unittest.TestCase):
    def test_keeps_a_quoted_finding_and_reads_only_the_main_logs(self):
        judge = FakeJudge([GOOD])
        found, cache = labs.find(FakeGH(world()), judge, CFG, {}, [])
        self.assertEqual([f["title"] for f in found], [GOOD["title"]])
        self.assertEqual(found[0]["url"], "https://example.com/articles/flink/")
        self.assertIn("LOG 05-fix.log", judge.prompts[0])
        self.assertNotIn("01-init.log", judge.prompts[0])
        self.assertIn("flink", cache)

    def test_drops_invented_evidence_and_repos_or_labels_outside_your_areas(self):
        bad = [dict(GOOD, evidence="Splunk +60, Flink +50"),
               dict(GOOD, repo="someone/else"),
               dict(GOOD, label="receiver/kafka"),
               dict(GOOD, kind="opinion")]
        found, _ = labs.find(FakeGH(world()), FakeJudge(bad), CFG, {}, [])
        self.assertEqual(found, [])

    def test_cached_until_the_article_or_its_output_changes(self):
        gh = FakeGH(world())
        _, cache = labs.find(gh, FakeJudge([GOOD]), CFG, {}, [])
        again = FakeJudge([GOOD])
        found, _ = labs.find(gh, again, CFG, cache, [])
        self.assertEqual(again.calls, 0)
        self.assertEqual(len(found), 1)
        changed = FakeJudge([])
        found, _ = labs.find(FakeGH(world(sha="a2")), changed, CFG, cache, [])
        self.assertEqual((changed.calls, found), (1, []))

    def test_flags_a_possible_duplicate_upstream(self):
        url = "https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/123"
        gh = FakeGH(world(), search=[{"html_url": url}])
        found, _ = labs.find(gh, FakeJudge([GOOD]), CFG, {}, [])
        self.assertEqual(found[0]["duplicate"], url)
        self.assertIn('label:"receiver/flinkmetrics"', gh.queries[0])

    def test_no_judge_shows_cached_findings_and_reads_nothing_new(self):
        gh = FakeGH(world())
        _, cache = labs.find(gh, FakeJudge([GOOD]), CFG, {}, [])
        found, _ = labs.find(gh, None, CFG, cache, [])
        self.assertEqual(len(found), 1)
        self.assertEqual(labs.find(gh, None, CFG, {}, [])[0], [])

    def test_off_without_a_labs_repo(self):
        self.assertEqual(labs.find(FakeGH({}), FakeJudge([GOOD]), dict(CFG, labs={}), {}, []), ([], {}))

    def test_board_lists_the_draft_to_file(self):
        found, _ = labs.find(FakeGH(world()), FakeJudge([dict(GOOD, body="has ``` fence")]), CFG, {}, [])
        d = Digest()
        d.board_labs = found
        text = "\n".join(render_labs(d, CFG))
        self.assertIn("Worth filing upstream, from your labs", text)
        self.assertIn("Draft to file", text)
        self.assertEqual(text.count("```"), 2)        # the body's own fence can't break the block
        self.assertEqual(render_labs(d, dict(CFG, labs={})), [])


if __name__ == "__main__":
    unittest.main()
