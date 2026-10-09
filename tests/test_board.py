import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import REPO, FakeWriter  # noqa: E402
from test_scout import CFG, NOW, world  # noqa: E402
from scout import board  # noqa: E402
from scout.cli import main  # noqa: E402
from scout.digest import build_digest  # noqa: E402

HOME = "Abhimanyu9988/oss-scout"


def baseline():
    gh = world()
    gh.issues_by_label["receiver/k8scluster"][0]["title"] = "ping @someone about semconv"
    return build_digest(gh, CFG, {}, NOW)


class RenderTest(unittest.TestCase):
    def test_board_lists_everything_politely(self):
        digest, _ = baseline()
        body = board.render_board(digest, CFG, NOW, HOME)
        self.assertIn("### Free in your areas (1)", body)
        self.assertIn("### Claimed by someone else but quiet for 45+ days (1)", body)
        self.assertIn("### PRs in your areas waiting 7+ days for a first review (2)", body)
        self.assertIn("https://redirect.github.com/open-telemetry/opentelemetry-collector-contrib/issues/51856", body)
        self.assertNotIn("](https://github.com/open-telemetry", body)   # no backlink-creating links
        self.assertNotRegex(body, r"(?<![\w/])#\d")                       # no bare #N autolinks
        self.assertIn("@​someone", body)                            # nobody else gets pinged
        self.assertIsNone(re.search(r"@someone", body))

    def test_comment_mentions_only_the_user(self):
        digest, _ = baseline()
        text = board.render_comment(digest, CFG, NOW)
        self.assertTrue(text.startswith("@Abhimanyu9988 · Wed 07 Oct"))
        self.assertIn("Watching from today", text)
        self.assertEqual(re.findall(r"@(\w+)", text), ["Abhimanyu9988"])


class PublishTest(unittest.TestCase):
    def test_creates_board_once_then_updates(self):
        digest, _ = baseline()
        w = FakeWriter()
        url = board.publish(w, HOME, digest, CFG, NOW)
        self.assertEqual(url, f"https://github.com/{HOME}/issues/1")
        self.assertEqual([c[0] for c in w.calls], ["find", "label", "create", "comment"])

        w2 = FakeWriter(existing={"number": 7, "html_url": "u"})
        board.publish(w2, HOME, digest, CFG, NOW, comment=False)
        self.assertEqual([c[0] for c in w2.calls], ["find", "update"])


class CliDeliveryTest(unittest.TestCase):
    def run_cli(self, writer, state=None, extra_env=None, cfg=None):
        tmp = tempfile.mkdtemp()
        cfg_path, state_path = os.path.join(tmp, "c.json"), os.path.join(tmp, "s.json")
        with open(cfg_path, "w") as fh:
            json.dump(cfg or dict(CFG, deliver=["github"]), fh)
        if state is not None:
            with open(state_path, "w") as fh:
                json.dump(state, fh)
        env = {"GITHUB_REPOSITORY": HOME}
        env.update(extra_env or {})
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = main(["--config", cfg_path, "digest", "--state", state_path], gh=world(), now=NOW, writer=writer)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return rc, out.getvalue(), err.getvalue(), os.path.exists(state_path)

    def test_first_run_creates_board_and_saves_state(self):
        w = FakeWriter()
        rc, out, _, saved = self.run_cli(w)
        self.assertEqual(rc, 0)
        self.assertIn("Board updated", out)
        self.assertTrue(saved)

    def test_quiet_day_updates_board_without_comment(self):
        _, state = build_digest(world(), CFG, {}, NOW)
        state["last_run"] = "2026-10-07T06:59:00Z"
        w = FakeWriter(existing={"number": 3, "html_url": "u"})
        rc, out, _, _ = self.run_cli(w, state=state)
        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in w.calls], ["find", "update"])
        self.assertIn("Nothing new today", out)

    def test_failed_delivery_does_not_save_state(self):
        rc, _, err, saved = self.run_cli(FakeWriter(fail=True))
        self.assertEqual(rc, 4)
        self.assertIn("delivery failed", err)
        self.assertFalse(saved)

    def test_default_delivery_is_github(self):
        w = FakeWriter()
        cfg = {k: v for k, v in CFG.items()}
        rc, _, _, _ = self.run_cli(w, cfg=cfg)
        self.assertEqual(rc, 0)
        self.assertTrue(any(c[0] == "create" for c in w.calls))


if __name__ == "__main__":
    unittest.main()


class FreshCopyTest(unittest.TestCase):
    def test_template_copy_adopts_its_owner(self):
        from scout.cli import adopt_fresh_copy
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "janedoe/oss-scout", "GITHUB_REPOSITORY_OWNER": "janedoe"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            cfg = dict(CFG, board_repo="Abhimanyu9988/oss-scout")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(adopt_fresh_copy(cfg))
            self.assertEqual((cfg["github_user"], cfg["board_repo"]), ("janedoe", "janedoe/oss-scout"))
            same_owner = dict(CFG, board_repo="janedoe/oss-scout-old")
            self.assertFalse(adopt_fresh_copy(same_owner))        # same owner, other repo: the old-copy error stays
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class WrongRepoTest(unittest.TestCase):
    def test_actions_run_in_another_repo_explains_itself(self):
        import contextlib as cl
        tmp = tempfile.mkdtemp()
        cfg_path, state_path = os.path.join(tmp, "c.json"), os.path.join(tmp, "s.json")
        with open(cfg_path, "w") as fh:
            json.dump(dict(CFG, deliver=["github"], board_repo=HOME), fh)
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": HOME + "-archive"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        err = io.StringIO()
        try:
            with cl.redirect_stdout(io.StringIO()), cl.redirect_stderr(err):
                rc = main(["--config", cfg_path, "digest", "--state", state_path], gh=world(), now=NOW, writer=FakeWriter())
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(rc, 2)
        self.assertIn("A workflow can only post to its own repository", err.getvalue())
        self.assertIn("gh workflow disable digest.yml -R Abhimanyu9988/oss-scout-archive", err.getvalue())
        self.assertFalse(os.path.exists(state_path))


class SizeTest(unittest.TestCase):
    def test_sections_are_capped(self):
        from scout.digest import Item
        digest, _ = baseline()
        digest.board_free = [Item(f"o/r#{n}", f"https://github.com/o/r/issues/{n}", "t", "") for n in range(40)]
        body = board.render_board(digest, dict(CFG, board_section_limit=10), NOW, HOME)
        self.assertIn("### Free in your areas (40)", body)
        self.assertIn("…and 30 more, not listed here", body)
        self.assertLess(len(body), board.BODY_LIMIT)

    def test_busy_board_moves_to_a_fresh_issue(self):
        digest, _ = baseline()
        w = FakeWriter(existing={"number": 7, "html_url": "u", "comments": 100})
        url = board.publish(w, HOME, digest, CFG, NOW, comment=False)
        self.assertEqual([c[0] for c in w.calls], ["find", "label", "create", "comment", "close"])
        self.assertIn("Continued in #1", w.calls[3][2])
        self.assertEqual(url, f"https://github.com/{HOME}/issues/1")
