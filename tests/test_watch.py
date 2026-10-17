import os
import re
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake import FakeGitHub, FakeWriter  # noqa: E402
from test_scout import CFG  # noqa: E402
from scout import board, slack, watch  # noqa: E402
from scout.digest import build_digest  # noqa: E402

NOW = datetime(2026, 12, 15, 7, 0, tzinfo=timezone.utc)
SIG = "kubernetes/sig-release"

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>dev</title>
<item><title>Kubernetes v1.39 Release Team Shadow Application is open</title>
  <link>https://groups.google.com/a/kubernetes.io/g/dev/c/abc</link>
  <pubDate>Mon, 14 Dec 2026 16:00:00 +0000</pubDate><description>Apply by Friday</description></item>
<item><title>v1.38 shadow retro notes</title>
  <link>https://groups.google.com/a/kubernetes.io/g/dev/c/old</link>
  <pubDate>Mon, 07 Sep 2026 16:00:00 +0000</pubDate><description></description></item>
<item><title>Code freeze reminder</title>
  <link>https://groups.google.com/a/kubernetes.io/g/dev/c/cf</link>
  <pubDate>Mon, 14 Dec 2026 10:00:00 +0000</pubDate><description>no match here</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>dev</title>
<entry><title>Release team shadows wanted</title>
  <link rel="alternate" href="https://example.org/a"/><updated>2026-12-10T09:00:00Z</updated>
  <summary>sign-up form for v1.39</summary></entry>
</feed>"""

WATCH = {
    "name": "Kubernetes v1.39 Release Team shadow application",
    "from": "2026-11-01",
    "until": "2027-01-31",
    "note": "Apply the same day.",
    "sources": [
        {"type": "github_path", "label": "v1.39 folder", "repo": SIG, "path": "releases/release-1.39"},
        {"type": "github_search", "label": "sig-release shadows", "query": f"repo:{SIG} 1.39 shadow"},
        {"type": "feed", "label": "dev@kubernetes.io", "url": "https://feed.example/rss", "all": ["shadow"]},
    ],
}


def search_hit(n, title, created="2026-12-12T10:00:00Z"):
    return {"number": n, "title": title, "html_url": f"https://github.com/{SIG}/issues/{n}",
            "repository_url": f"https://api.github.com/repos/{SIG}", "created_at": created}


def world(found=True):
    gh = FakeGitHub()
    if found:
        gh.paths.add(f"{SIG}/releases/release-1.39")
        gh.searches.append(("1.39 shadow", [search_hit(3001, "v1.39 Release Team shadow selection #3002")]))
    return gh


def fetcher(raw=RSS):
    return lambda url: raw


def cfg(*watches):
    return dict(CFG, watches=list(watches))


class SourcesTest(unittest.TestCase):
    def test_all_three_sources_find_their_hit(self):
        gh = world()
        (res,) = watch.check_all(gh, cfg(WATCH), NOW, fetch=fetcher())
        self.assertEqual(res.errors, [])
        self.assertEqual([h.key for h in res.hits], [
            f"path:{SIG}/releases/release-1.39",
            f"{SIG}#3001",
            "feed:https://groups.google.com/a/kubernetes.io/g/dev/c/abc",
        ])
        self.assertEqual(res.hits[0].url, f"https://github.com/{SIG}/tree/HEAD/releases/release-1.39")
        self.assertIn("created:>=2026-11-01", gh.queries[0])

    def test_nothing_yet(self):
        (res,) = watch.check_all(world(found=False), cfg(WATCH), NOW, fetch=fetcher(b"<rss><channel/></rss>"))
        self.assertEqual((res.hits, res.errors), ([], []))

    def test_feed_skips_entries_before_from_and_without_the_words(self):
        hits = watch._feed_hits(fetcher(), WATCH["sources"][2], datetime(2026, 11, 1).date(), "dev")
        self.assertEqual([h.title for h in hits], ["Kubernetes v1.39 Release Team Shadow Application is open"])
        self.assertEqual(hits[0].when, "2026-12-14")

    def test_atom_and_any_words(self):
        src = {"type": "feed", "url": "x", "all": ["shadow"], "any": ["1.39", "1.40"]}
        hits = watch._feed_hits(fetcher(ATOM), src, None, "atom")
        self.assertEqual([(h.url, h.when) for h in hits], [("https://example.org/a", "2026-12-10")])
        self.assertEqual(watch._feed_hits(fetcher(ATOM), dict(src, any=["1.40"]), None, "atom"), [])

    def test_broken_feed_is_reported_not_raised(self):
        (res,) = watch.check_all(world(), cfg(WATCH), NOW, fetch=fetcher(b"<html>not a feed"))
        self.assertEqual(len(res.hits), 2)
        self.assertTrue(res.errors and res.errors[0].startswith("dev@kubernetes.io: not a valid feed"))

    def test_only_https_feeds_and_web_links(self):
        with self.assertRaises(watch.WatchError):
            watch.fetch_url("file:///etc/passwd")
        raw = RSS.replace(b"https://groups.google.com/a/kubernetes.io/g/dev/c/abc", b"javascript:alert(1)")
        self.assertEqual(watch._feed_hits(fetcher(raw), WATCH["sources"][2], None, "dev")[0].url,
                         "https://groups.google.com/a/kubernetes.io/g/dev/c/old")   # only the other match is kept

    def test_feed_title_cannot_inject_a_link(self):
        hit = watch.Hit("feed:x", "https://example.org/a", "shadow](https://evil.example) [x", "dev")
        line = board._watch_line(hit)
        self.assertEqual(line.count("]("), 1)
        self.assertNotIn("](https://evil.example)", line)

    def test_expired_watch_is_skipped(self):
        self.assertEqual(watch.check_all(world(), cfg(dict(WATCH, until="2026-12-01")), NOW, fetch=fetcher()), [])

    def test_unknown_source_type(self):
        (res,) = watch.check_all(world(), cfg({"name": "x", "sources": [{"type": "slack"}]}), NOW, fetch=fetcher())
        self.assertEqual(res.errors, ["slack: unknown source type 'slack'"])


class DigestTest(unittest.TestCase):
    def test_new_hits_are_announced_once_even_on_the_first_run(self):
        digest, state = build_digest(world(), cfg(WATCH), {}, NOW, fetch=fetcher())
        self.assertEqual(len(digest.watch_new), 3)
        self.assertFalse(digest.is_empty())
        comment = board.render_comment(digest, cfg(WATCH), NOW)
        self.assertIn("### 🔔 Kubernetes v1.39 Release Team shadow application", comment)
        self.assertIn("https://groups.google.com/a/kubernetes.io/g/dev/c/abc", comment)

        later, _ = build_digest(world(), cfg(WATCH), state, NOW, fetch=fetcher())
        self.assertEqual(later.watch_new, [])
        self.assertEqual(sum(len(r.hits) for r in later.watches), 3)   # still listed on the board

    def test_board_shows_watch_politely(self):
        digest, _ = build_digest(world(), cfg(WATCH), {}, NOW, fetch=fetcher())
        body = board.render_board(digest, cfg(WATCH), NOW, "Abhimanyu9988/oss-scout")
        self.assertIn("## Watching", body)
        self.assertIn("### Kubernetes v1.39 Release Team shadow application (until 2027-01-31)", body)
        self.assertIn("**3 watch hits**", body)
        self.assertIn(f"https://redirect.github.com/{SIG}/issues/3001", body)
        self.assertNotIn(f"](https://github.com/{SIG}/issues", body)
        body = re.sub(r"`[^`]*`", "", body)
        self.assertNotRegex(body, r"(?<![\w&])#\d")                    # the "#3002" in the title is broken up

    def test_board_shows_nothing_yet_and_errors(self):
        digest, _ = build_digest(world(found=False), cfg(WATCH), {}, NOW, fetch=fetcher(b"oops"))
        body = board.render_board(digest, cfg(WATCH), NOW, "Abhimanyu9988/oss-scout")
        self.assertIn("_Nothing yet._", body)
        self.assertIn("_Couldn't check dev@\u200bkubernetes.io: not a valid feed", body)   # "@" never pings
        self.assertEqual(digest.errors, [])     # a watch failing doesn't mark the whole run partial

    def test_no_watches_configured_changes_nothing(self):
        digest, state = build_digest(world(), CFG, {}, NOW)
        self.assertEqual((digest.watches, digest.watch_new, state["seen"]["watch"]), ([], [], []))
        self.assertNotIn("## Watching", board.render_board(digest, CFG, NOW))

    def test_slack_lists_new_hits(self):
        digest, _ = build_digest(world(), cfg(WATCH), {}, NOW, fetch=fetcher())
        text = slack.to_plain_text(slack.build_payload(digest, "Scout", "Tue 15 Dec"))
        self.assertIn("*Watch*", text)
        self.assertIn("<https://github.com/kubernetes/sig-release/issues/3001|", text)


class PublicIssueTest(unittest.TestCase):
    HOME = "Abhimanyu9988/oss-scout"
    PUBLIC = dict(WATCH, public_issue=True)

    def run_publish(self, writer, state=None, watches=None, now=NOW):
        c = cfg(*(watches if watches is not None else [self.PUBLIC]))
        digest, new_state = build_digest(world(), c, state or {}, now, fetch=fetcher())
        board.publish_watches(writer, self.HOME, digest, c, now)
        return digest, new_state

    def test_first_run_creates_the_issue_and_links_it_from_the_board(self):
        w = FakeWriter()
        digest, _ = self.run_publish(w)
        (create,) = [c for c in w.calls if c[0] == "create"]
        self.assertEqual(create[1], "Watch: Kubernetes v1.39 Release Team shadow application")
        self.assertEqual(create[3], ["oss-scout-watch"])
        self.assertIn("Press **Subscribe**", create[2])
        self.assertIn(f"https://redirect.github.com/{SIG}/issues/3001", create[2])
        self.assertNotIn("@Abhimanyu9988", create[2])              # subscribers only, nobody is mentioned
        self.assertFalse([c for c in w.calls if c[0] == "comment"])   # nothing to tell subscribers yet
        body = board.render_board(digest, cfg(self.PUBLIC), NOW, self.HOME)
        self.assertIn("[subscribe to its issue](https://github.com/Abhimanyu9988/oss-scout/issues/1)", body)

    def test_new_hits_are_commented_on_the_existing_issue(self):
        issue = {"number": 5, "title": "Watch: Kubernetes v1.39 Release Team shadow application",
                 "html_url": "https://github.com/Abhimanyu9988/oss-scout/issues/5"}
        w = FakeWriter(watch_issues=[issue])
        self.run_publish(w)
        self.assertIn(("update", 5), [c[:2] for c in w.calls])
        (comment,) = [c for c in w.calls if c[0] == "comment"]
        self.assertTrue(comment[2].startswith("New:"))
        self.assertIn("groups.google.com/a/kubernetes.io/g/dev/c/abc", comment[2])

        _, state = self.run_publish(FakeWriter(watch_issues=[issue]))
        w = FakeWriter(watch_issues=[issue])
        self.run_publish(w, state=state)
        self.assertFalse([c for c in w.calls if c[0] == "comment"])   # nothing new, no comment

    def test_issue_is_closed_when_the_watch_ends(self):
        issue = {"number": 5, "title": "Watch: Kubernetes v1.39 Release Team shadow application", "html_url": "u"}
        w = FakeWriter(watch_issues=[issue])
        self.run_publish(w, now=datetime(2027, 2, 1, 7, 0, tzinfo=timezone.utc))
        self.assertIn(("close", 5), w.calls)
        self.assertIn(("comment", 5, "This watch has ended, so this issue is closed."), w.calls)

    def test_private_watch_gets_no_issue(self):
        w = FakeWriter()
        self.run_publish(w, watches=[WATCH])
        self.assertFalse([c for c in w.calls if c[0] in ("create", "label")])


if __name__ == "__main__":
    unittest.main()
