"""The optional AI second look at free issues. Uses a fake model; never calls the real API."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import REPO, comment, xref  # noqa: E402
from test_scout import CFG, NOW, world  # noqa: E402
from scout import ai, board  # noqa: E402
from scout.classify import CLAIM_RE  # noqa: E402
from scout.cli import main  # noqa: E402
from scout.digest import build_digest  # noqa: E402

FREE = f"{REPO}#51856"            # the one issue the rules call free in the test world


class FakeModel:
    """Stands in for the HTTP call; answers with a fixed verdict and records prompts."""

    def __init__(self, answer='{"verdict": "taken", "reason": "author has a draft branch"}', fail=None):
        self.answer, self.fail, self.prompts, self.headers = answer, fail, [], []

    def __call__(self, url, headers, payload, timeout=60):
        if self.fail:
            raise ai.AIError(self.fail)
        self.headers.append(headers)
        self.prompts.append(payload["messages"][0]["content"])
        return {"content": [{"type": "text", "text": self.answer}], "stop_reason": "end_turn"}


def judge(model, max_calls=40):
    return ai.Judge("test-key", max_calls=max_calls, post=model)


class ParseTest(unittest.TestCase):
    def test_reads_json_even_with_text_around_it(self):
        self.assertEqual(ai.parse_verdict('Sure.\n{"verdict": "Not_Ready", "reason": "needs a  SIG decision"}'),
                         ("not_ready", "needs a SIG decision"))

    def test_rejects_unknown_or_missing_verdicts(self):
        for text in ("no json here", '{"verdict": "maybe"}', "{broken"):
            with self.assertRaises(ai.AIError):
                ai.parse_verdict(text)


class StopReasonTest(unittest.TestCase):
    @staticmethod
    def stopped(text):
        return lambda url, headers, payload, timeout=60: (
            {"content": [{"type": "text", "text": text}], "stop_reason": "max_tokens"})

    def test_sends_the_configured_cap(self):
        seen = []
        post = lambda url, headers, payload, timeout=60: seen.append(payload) or (
            {"content": [{"type": "text", "text": '{"verdict": "free", "reason": "x"}'}], "stop_reason": "end_turn"})
        ai.Judge("k", post=post).ask("p")
        ai.Judge("k", post=post, max_tokens=300).ask("p")
        self.assertEqual([p["max_tokens"] for p in seen], [ai.MAX_TOKENS, 300])

    def test_cut_off_after_the_json_still_counts(self):
        j = ai.Judge("k", post=self.stopped('{"verdict": "taken", "reason": "draft PR"}\nAlso, the issue'))
        self.assertEqual(j.ask("p"), ("taken", "draft PR"))

    def test_cut_off_before_the_json_reports_what_it_wrote(self):
        j = ai.Judge("k", post=self.stopped("Let me look at the comments first. The author said"))
        with self.assertRaises(ai.AIError) as ctx:
            j.ask("p")
        self.assertIn("max_tokens (1024)", str(ctx.exception))
        self.assertIn("Let me look at the comments", str(ctx.exception))


class ReviewTest(unittest.TestCase):
    def test_set_aside_items_leave_free_and_get_their_own_section(self):
        model = FakeModel()
        digest, state = build_digest(world(), CFG, {}, NOW, judge=judge(model))
        self.assertNotIn(FREE, [i.key for i in digest.board_free + digest.to_pick])
        self.assertEqual([i.key for i in digest.board_aside], [FREE])
        self.assertIn("someone has it: author has a draft branch", digest.board_aside[0].detail)
        self.assertEqual(state["ai_cache"][FREE]["verdict"], "taken")
        self.assertEqual(model.headers[0]["anthropic-version"], "2023-06-01")
        body = board.render_board(digest, CFG, NOW)
        self.assertIn("Free by the rules, but the AI check says not to start (1)", body)
        self.assertIn("1 set aside by AI", body)

    def test_free_verdict_keeps_it_free(self):
        model = FakeModel('{"verdict": "free", "reason": "nobody has started"}')
        digest, _ = build_digest(world(), CFG, {}, NOW, judge=judge(model))
        self.assertIn(FREE, [i.key for i in digest.board_free])
        self.assertEqual(digest.board_aside, [])

    def test_cached_until_the_issue_changes(self):
        model = FakeModel()
        _, state = build_digest(world(), CFG, {}, NOW, judge=judge(model))
        model2 = FakeModel('{"verdict": "free", "reason": "x"}')
        digest, _ = build_digest(world(), CFG, state, NOW, judge=judge(model2))
        self.assertEqual(model2.prompts, [])                         # nothing changed, no calls
        self.assertEqual([i.key for i in digest.board_aside], [FREE])
        gh = world()
        gh.issues_by_label["receiver/k8scluster"][0]["updated_at"] = "2026-10-06T00:00:00Z"
        build_digest(gh, CFG, state, NOW, judge=judge(model2))
        self.assertEqual(len(model2.prompts), 1)                      # changed issue is judged again

    def test_errors_and_budget_leave_issues_free(self):
        digest, _ = build_digest(world(), CFG, {}, NOW, judge=judge(FakeModel(fail="HTTP 401 from the Anthropic API")))
        self.assertIn(FREE, [i.key for i in digest.board_free])
        self.assertTrue(any("AI check" in e for e in digest.errors))
        digest, _ = build_digest(world(), CFG, {}, NOW, judge=judge(FakeModel(), max_calls=0))
        self.assertIn(FREE, [i.key for i in digest.board_free])
        self.assertEqual(digest.errors, [])

    def test_prompt_carries_description_comments_and_prs(self):
        from scout.classify import Candidate
        from fake import issue
        raw = dict(issue(7, "Expose pod owner", ["processor/k8sattributes"], author="stuart23"),
                   body="Expose k8s.owner.kind", created_at="2026-08-13T00:00:00Z")
        events = [comment("stuart23", "I have a draft to solve this here", "2026-08-13T00:00:00Z"),
                  xref(50556, "closed")]
        prompt = ai.build_prompt(Candidate.from_issue(REPO, raw, "processor/k8sattributes"), events, NOW)
        for part in ("Author: stuart23", "Opened 55 days ago", "Expose k8s.owner.kind",
                     "comment by stuart23", "I have a draft", "#50556 (closed"):
            self.assertIn(part, prompt)


class SetupTest(unittest.TestCase):
    def test_off_without_a_key_or_when_disabled(self):
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertIsNone(ai.Judge.from_env(CFG))
            os.environ["ANTHROPIC_API_KEY"] = "k"
            self.assertIsNotNone(ai.Judge.from_env(CFG))
            self.assertIsNone(ai.Judge.from_env(dict(CFG, ai={"enabled": False})))
            self.assertEqual(ai.Judge.from_env(dict(CFG, ai={"model": "m", "max_calls": 5})).max_calls, 5)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old

    def test_cli_runs_with_an_injected_judge(self):
        tmp = tempfile.mkdtemp()
        cfg_path, state_path = os.path.join(tmp, "c.json"), os.path.join(tmp, "s.json")
        with open(cfg_path, "w") as fh:
            json.dump(CFG, fh)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["--config", cfg_path, "digest", "--dry-run", "--state", state_path],
                      gh=world(), now=NOW, judge=judge(FakeModel()))
        self.assertEqual(rc, 0)
        self.assertIn("AI check: 1 call(s), 1 issue(s) set aside", out.getvalue())


class DraftClaimTest(unittest.TestCase):
    def test_having_a_draft_or_branch_is_a_claim(self):
        for text in ("I have a draft to solve this", "I've a branch for this", "I have a prototype"):
            self.assertIsNotNone(CLAIM_RE.search(text), text)
        self.assertIsNone(CLAIM_RE.search("I have a question about this"))


if __name__ == "__main__":
    unittest.main()
