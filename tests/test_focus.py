"""Watching a whole repo narrowed by keywords and file paths, and the ChatGPT judge.
Uses fakes only; never calls GitHub or a model API."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import FakeGitHub, comment, issue, pr  # noqa: E402
from test_scout import NOW, USER  # noqa: E402
from scout import ai, board  # noqa: E402
from scout.digest import build_digest, new_issues, scan_candidates, unreviewed_prs  # noqa: E402

DP = "opensearch-project/data-prepper"
PATHS = ["data-prepper-plugins/otel-", "data-prepper-plugins/otlp-", "data-prepper-plugins/prometheus-"]
CFG = {"github_user": USER, "quiet_days": 45, "stale_review_days": 7,
       "repos": [{"repo": DP, "keywords": ["otel", "otlp", "opentelemetry", "prometheus"],
                  "paths": PATHS, "stale_review_days": 2}]}


def dp_world():
    issues = [issue(7220, "When spans are dropped by otel_trace_source, add more details", ["untriaged"]),
              issue(7211, "[BUG] CloudWatch Logs sink acknowledges undelivered events", ["bug"]),
              issue(7142, "Support parquet indices", ["OTel"])]          # matched by its label
    prs = [pr(7148, "Upgrade OpenTelemetry proto Java bindings", "KarstenSchnitter", created="2026-10-01T00:00:00Z"),
           pr(7169, "Fix service map skipping traces with multiple workers", "TackAdam",
              created="2026-10-01T00:00:00Z"),
           pr(7156, "Add S3-compatible storage support to the s3 sink", "Ystk-hsn", created="2026-10-01T00:00:00Z"),
           pr(7230, "Fix span drop logging", "someone", created="2026-10-06T12:00:00Z")]   # under a day old
    gh = FakeGitHub(issues_by_label={None: issues}, prs_by_label={None: prs})
    gh.files = {7169: ["data-prepper-plugins/otel-apm-service-map-processor/src/main/X.java"],
                7156: ["data-prepper-plugins/s3-sink/src/main/Y.java"]}
    return gh


class FocusTest(unittest.TestCase):
    def test_issues_must_name_a_keyword_in_the_title_or_labels(self):
        gh = dp_world()
        cands = scan_candidates(gh, CFG, NOW, [])
        self.assertEqual(sorted(c.number for c in cands), [7142, 7220])
        self.assertEqual(gh.open_calls, [(DP, None, 10)])                  # reads deeper than a label listing

    def test_prs_match_by_title_or_by_the_files_they_touch(self):
        gh = dp_world()
        prs = {}
        scan_candidates(gh, CFG, NOW, [], prs)
        queue = unreviewed_prs(gh, CFG, NOW, prs, set(), [])
        self.assertEqual(sorted(queue), [f"{DP}#7148", f"{DP}#7169"])
        self.assertEqual(sorted(gh.file_calls), [7156, 7169])              # title match needs no file read
        self.assertNotIn(7230, gh.file_calls)                              # too young to check at all

    def test_a_repo_can_set_its_own_review_wait(self):
        gh = dp_world()
        prs = {}
        scan_candidates(gh, CFG, NOW, [], prs)
        cfg = dict(CFG, repos=[dict(CFG["repos"][0], stale_review_days=10)])
        self.assertEqual(unreviewed_prs(gh, cfg, NOW, prs, set(), []), {})

    def test_board_heading_gives_the_shortest_wait(self):
        digest, _ = build_digest(dp_world(), CFG, {}, NOW)
        self.assertIn("PRs in your areas waiting 2+ days for a first review", board.render_board(digest, CFG, NOW))

    def test_labelled_repos_are_unchanged(self):
        cfg = {"github_user": USER, "repos": [{"repo": DP, "labels": ["bug"], "keywords": ["otel"]}]}
        gh = FakeGitHub(issues_by_label={"bug": [issue(7211, "CloudWatch sink bug", ["bug"])]})
        self.assertEqual([c.number for c in scan_candidates(gh, cfg, NOW, [])], [7211])


class TriageTest(unittest.TestCase):
    def test_new_issues_use_the_repos_own_keywords_and_flag_untriaged(self):
        cfg = dict(CFG, triage={"days": 7, "keywords": ["kubelet"]})
        new = [dict(issue(7220, "When spans are dropped by otel_trace_source", ["untriaged"]),
                    created_at="2026-10-06T00:00:00Z"),
               dict(issue(7211, "kubelet-ish but not ours: CloudWatch sink", ["untriaged"]),
                    created_at="2026-10-06T00:00:00Z")]
        gh = FakeGitHub(searches=[(f"repo:{DP}", new)],
                        comments={7220: [comment("dlvenable", "Thanks, we'll discuss in triage", "2026-10-06T10:00:00Z")]})
        found, _ = new_issues(gh, cfg, NOW, {}, [])
        self.assertEqual(list(found), [f"{DP}#7220"])
        self.assertIn("untriaged", found[f"{DP}#7220"][0].detail)


class FakeOpenAI:
    def __init__(self, content='{"verdict": "taken", "reason": "maintainer has a branch"}', finish="stop",
                 refusal=None):
        self.content, self.finish, self.refusal, self.calls = content, finish, refusal, []

    def __call__(self, url, headers, payload, timeout=60):
        self.calls.append((url, headers, payload))
        return {"choices": [{"message": {"content": self.content, "refusal": self.refusal},
                             "finish_reason": self.finish}]}


class OpenAITest(unittest.TestCase):
    def judge(self, model):
        return ai.Judge("sk-test", model="gpt-5-mini", provider="openai", post=model, max_tokens=4000)

    def test_sends_a_chat_completions_request_and_reads_the_verdict(self):
        model = FakeOpenAI()
        self.assertEqual(self.judge(model).ask("p"), ("taken", "maintainer has a branch"))
        url, headers, payload = model.calls[0]
        self.assertEqual(url, ai.OPENAI_API)
        self.assertEqual(headers["authorization"], "Bearer sk-test")
        self.assertEqual(payload["model"], "gpt-5-mini")
        self.assertEqual(payload["max_completion_tokens"], 4000)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])

    def test_cut_off_and_refusal(self):
        with self.assertRaises(ai.AIError) as ctx:
            self.judge(FakeOpenAI(content="", finish="length")).ask("p")
        self.assertIn("max_tokens (4000)", str(ctx.exception))
        with self.assertRaises(ai.AIError):
            self.judge(FakeOpenAI(content=None, refusal="I can't help with that")).ask("p")

    def test_a_claude_model_name_falls_back_to_the_judges_model(self):
        model = FakeOpenAI(content='{"findings": []}')
        self.judge(model).ask_json("sys", "p", model="claude-sonnet-5-5")
        self.assertEqual(model.calls[0][2]["model"], "gpt-5-mini")

    def test_provider_choice_from_env(self):
        saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
        try:
            self.assertIsNone(ai.Judge.from_env(CFG))
            os.environ["OPENAI_API_KEY"] = "sk"
            j = ai.Judge.from_env(CFG)
            self.assertEqual((j.provider, j.model, j.max_tokens), ("openai", "gpt-5-mini", ai.OPENAI_MAX_TOKENS))
            os.environ["ANTHROPIC_API_KEY"] = "a"
            self.assertEqual(ai.Judge.from_env(CFG).provider, "anthropic")          # Anthropic first by default
            self.assertEqual(ai.Judge.from_env(dict(CFG, ai={"provider": "openai"})).provider, "openai")
            del os.environ["OPENAI_API_KEY"]
            self.assertIsNone(ai.Judge.from_env(dict(CFG, ai={"provider": "openai"})))  # no silent switch
            with self.assertRaises(ai.AIError):
                ai.Judge.from_env(dict(CFG, ai={"provider": "gemini"}))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
