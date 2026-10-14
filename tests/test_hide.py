"""Hiding items from the board with /hide and /unhide replies on the board issue."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import REPO, FakeWriter  # noqa: E402
from test_scout import CFG, NOW, USER, world  # noqa: E402
from scout import board, hide  # noqa: E402
from scout.cli import main  # noqa: E402
from scout.digest import build_digest  # noqa: E402

HOME = "Abhimanyu9988/oss-scout"
FREE = f"{REPO}#51856"          # the one free issue in the test world


def reply(cid, body, who=USER):
    return {"id": cid, "user": {"login": who}, "body": body}


class ParseTest(unittest.TestCase):
    def test_reference_forms(self):
        refs = hide.parse_refs("semantic-conventions#3119, open-telemetry/opentelemetry-collector#16164 "
                               "https://github.com/open-telemetry/semantic-conventions/pull/4158 "
                               "https://redirect.github.com/kubernetes/kubernetes/issues/12")
        self.assertEqual(refs, [(None, "semantic-conventions", 3119),
                                ("open-telemetry", "opentelemetry-collector", 16164),
                                ("open-telemetry", "semantic-conventions", 4158),
                                ("kubernetes", "kubernetes", 12)])

    def test_only_your_new_command_lines_count(self):
        comments = [reply(5, "/hide a#1"), reply(9, "/hide a#2", who="someone-else"),
                    reply(10, "thanks!\n/hide a#3\n  /unhide a#1"), reply(11, "no command here")]
        self.assertEqual(hide.read_commands(comments, USER, after_id=5),
                         [(10, [("hide", [(None, "a", 3)]), ("unhide", [(None, "a", 1)])])])


class ApplyTest(unittest.TestCase):
    KNOWN = {"open-telemetry/semantic-conventions#3119", "open-telemetry/opentelemetry-collector#3119"}
    REPOS = ["open-telemetry/opentelemetry-collector-contrib"]

    def test_short_names_resolve_against_the_board(self):
        hidden, reactions = hide.apply([], [(1, [("hide", [(None, "semantic-conventions", 3119)])])],
                                       self.KNOWN, self.REPOS)
        self.assertEqual(hidden, ["open-telemetry/semantic-conventions#3119"])
        self.assertEqual(reactions, [(1, "+1")])

    def test_short_names_resolve_against_configured_repos_too(self):
        hidden, _ = hide.apply([], [(1, [("hide", [(None, "opentelemetry-collector-contrib", 7)])])],
                               set(), self.REPOS)
        self.assertEqual(hidden, ["open-telemetry/opentelemetry-collector-contrib#7"])

    def test_unknown_reference_gets_a_confused_reaction(self):
        hidden, reactions = hide.apply([], [(1, [("hide", [(None, "nope", 1)])])], self.KNOWN, self.REPOS)
        self.assertEqual(hidden, [])
        self.assertEqual(reactions, [(1, "confused")])

    def test_unhide_and_replay_in_order(self):
        cmds = [(1, [("hide", [("o", "r", 1), ("o", "r", 2)])]), (2, [("unhide", [("o", "r", 1)])])]
        hidden, reactions = hide.apply(["o/r#9"], cmds, set(), [])
        self.assertEqual(hidden, ["o/r#2", "o/r#9"])
        self.assertEqual(reactions, [(1, "+1"), (2, "+1")])


class BoardGitHub:
    """world() plus an existing board issue with replies."""

    def __init__(self, replies):
        self.inner = world()
        self.replies = replies

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def find_open_issue(self, repo, label):
        return {"number": 1, "comments": len(self.replies), "html_url": f"https://github.com/{repo}/issues/1"}

    def issue_comments(self, repo, number):
        return self.replies if number == 1 else self.inner.issue_comments(repo, number)


class ProcessTest(unittest.TestCase):
    def test_hidden_items_leave_every_section_and_are_listed_at_the_end(self):
        gh = BoardGitHub([reply(100, f"/hide opentelemetry-collector-contrib#51856")])
        digest, new_state = build_digest(gh, CFG, {}, NOW)
        self.assertIn(FREE, [i.key for i in digest.board_free])
        reactions = hide.process(gh, CFG, {}, new_state, digest, HOME)
        self.assertEqual(reactions, [(100, "+1")])
        self.assertNotIn(FREE, [i.key for i in digest.board_free + digest.to_pick])
        self.assertEqual(new_state["hidden"], [FREE])
        self.assertEqual(new_state["hide_cursor"], 100)
        body = board.render_board(digest, CFG, NOW, HOME)
        self.assertIn("<summary><b>Hidden (1)</b></summary>", body)
        self.assertIn("redirect.github.com/open-telemetry/opentelemetry-collector-contrib/issues/51856", body)

    def test_old_commands_are_not_replayed_and_hidden_list_persists(self):
        gh = BoardGitHub([reply(100, "/unhide opentelemetry-collector-contrib#51856")])
        digest, new_state = build_digest(gh, CFG, {}, NOW)
        reactions = hide.process(gh, CFG, {"hidden": [FREE], "hide_cursor": 100}, new_state, digest, HOME)
        self.assertEqual(reactions, [])
        self.assertEqual(new_state["hidden"], [FREE])

    def test_dry_run_applies_but_leaves_comments_for_the_real_run(self):
        gh = BoardGitHub([reply(100, "/hide opentelemetry-collector-contrib#51856")])
        digest, new_state = build_digest(gh, CFG, {}, NOW)
        hide.process(gh, CFG, {"hide_cursor": 50}, new_state, digest, HOME, advance=False)
        self.assertEqual(new_state["hidden"], [FREE])
        self.assertEqual(new_state["hide_cursor"], 50)


class CliTest(unittest.TestCase):
    def test_board_run_reacts_to_processed_replies(self):
        tmp = tempfile.mkdtemp()
        cfg_path, state_path = os.path.join(tmp, "c.json"), os.path.join(tmp, "s.json")
        with open(cfg_path, "w") as fh:
            json.dump(dict(CFG, deliver=["github"], board_repo=HOME), fh)
        gh = BoardGitHub([reply(100, "/hide opentelemetry-collector-contrib#51856"), reply(101, "/hide what#")])
        writer = FakeWriter(existing={"number": 1, "comments": 2, "html_url": f"https://github.com/{HOME}/issues/1"})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["--config", cfg_path, "digest", "--state", state_path], gh=gh, now=NOW, writer=writer)
        self.assertEqual(rc, 0)
        self.assertIn(("react", 100, "+1"), writer.calls)
        self.assertIn(("react", 101, "confused"), writer.calls)
        body = next(c[2] for c in writer.calls if c[0] == "update")
        self.assertNotIn("issues/51856) ", body.split("<summary><b>Hidden")[0])
        with open(state_path) as fh:
            state = json.load(fh)
        self.assertEqual(state["hidden"], [FREE])
        self.assertEqual(state["hide_cursor"], 101)


if __name__ == "__main__":
    unittest.main()
