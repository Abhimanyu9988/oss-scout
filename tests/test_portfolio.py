import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_contrib_log import C, CFG, TODAY, USER, FakeGH  # noqa: E402
from scout import contrib_log, portfolio  # noqa: E402
from scout.cli import main  # noqa: E402

NOTES = {
    "categories": {
        "original-work": {"title": "Original contributions", "description": "Accepted work."},
        "reviewing": {"title": "Reviewing others' work"},
        "leadership": "Leading and critical roles",
        "speaking": {"title": "Talks and writing"},
    },
    "auto": {"pr_merged": "original-work", "review": "reviewing", "extra:talk": "speaking", "extra:review": "reviewing"},
    "org_names": {"open-telemetry": "OpenTelemetry"},
    "items": {f"https://github.com/{C}/pull/51657": {"tags": ["leadership"], "note": "Private context here"}},
    "extras": [{"date": "2026-11-20", "type": "review", "title": "Program committee, Example Conf"},
               {"date": "2026-11-24", "type": "talk", "title": "A talk", "url": "https://example.org/t"}],
}


class PortfolioTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        records, _ = contrib_log.collect(FakeGH(), CFG, [], TODAY, full=True)
        self.data = os.path.join(self.dir, "contributions.json")
        with open(self.data, "w") as fh:
            json.dump({"user": USER, "records": records}, fh)
        self.notes = os.path.join(self.dir, "notes.json")
        with open(self.notes, "w") as fh:
            json.dump(NOTES, fh)

    def build(self):
        return portfolio.build(self.data, self.notes)

    def test_categories_from_rules_and_private_tags(self):
        records, notes, user = self.build()
        by_url = {r["url"]: r for r in records}
        pr = by_url[f"https://github.com/{C}/pull/51657"]
        self.assertEqual(pr["categories"], ["original-work", "leadership"])
        self.assertEqual(pr["private_note"], "Private context here")
        self.assertEqual(by_url[f"https://github.com/{C}/pull/50135#pullrequestreview-1"]["categories"], ["reviewing"])
        self.assertEqual(user, USER)

    def test_render(self):
        records, notes, user = self.build()
        text = portfolio.render(records, notes, user)
        self.assertIn("# Contribution portfolio · Abhimanyu9988", text)
        self.assertIn("Data through 2026-11-24.", text)
        self.assertIn("| Merged pull requests | 2 (+71 −2) |", text)
        self.assertIn("| Reviews of others' pull requests | 1 on 1 PR by 1 author |", text)
        self.assertIn("| Maintainers who approved or merged your work | 3 |", text)
        self.assertIn("Active in OpenTelemetry since September 2026: 2 merged pull requests in "
                      "internal/k8sinventory and receiver/sqlserver, reviewed and accepted by 3 maintainers.", text)
        self.assertIn("Resolved 2 reported issues through merged fixes.", text)
        self.assertIn("Reviewed 1 pull request from 1 other contributor (receiver/sqlserver).", text)
        self.assertIn("Gave 1 talk on this work.", text)
        self.assertIn("Served on 1 program committee or review panel.", text)
        self.assertIn("### Original contributions (2)", text)
        self.assertIn("### Leading and critical roles (1)", text)
        self.assertIn("  - “Great fix, thanks!” — TylerHelmuth", text)
        self.assertIn("  - Note: Private context here", text)
        self.assertIn("## Recognition from peers", text)
        self.assertIn("| [TylerHelmuth](https://github.com/TylerHelmuth) | approved your PRs (1) · merged your PRs (1) · "
                      "praised your work (1) |", text)
        self.assertIn("| [Srikar](https://github.com/Srikar) | you reviewed their PRs (1) |", text)
        self.assertIn("| 2026-11 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |", text)
        self.assertIn("| 2026-10 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |", text)      # #51657 opened in Sep, merged in Oct
        self.assertIn("| 2026-09 | 3 | 1 | 1 | 1 | 1 | 0 | 0 |", text)
        self.assertIn("I contribute to OpenTelemetry, mostly internal/k8sinventory and receiver/sqlserver.", text)
        self.assertIn("[opentelemetry-collector-contrib#51657](", text)
        self.assertIn("Document TLS | handshake &lt;issue&gt;", text)
        self.assertIn("## Not yet in a category", text)        # the open PR and the issue have no rule
        self.assertNotIn("issuecomment-2", text.split("## Not yet in a category")[1])  # own replies aren't nagged

    def test_csv(self):
        records, _, _ = self.build()
        text = portfolio.to_csv(records)
        header = text.splitlines()[0]
        self.assertEqual(header.split(","), portfolio.CSV_FIELDS)
        row = next(line for line in text.splitlines() if "pull/51657," in line)
        self.assertIn("original-work;leadership", row)
        self.assertIn("TylerHelmuth;krisztianfekete", row)

    def test_cli_is_idempotent(self):
        out_md = os.path.join(self.dir, "portfolio.md")
        out_csv = os.path.join(self.dir, "portfolio.csv")
        argv = ["portfolio", "--data", self.data, "--notes", self.notes, "--out", out_md, "--csv", out_csv]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(argv), 0)
        self.assertIn("Changed:", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(argv)
        self.assertIn("No changes.", buf.getvalue())

    def test_private_extras_never_reach_the_public_log(self):
        cfg = dict(CFG)
        readme = os.path.join(self.dir, "README.md")
        contrib_log.run(FakeGH(), cfg, TODAY, base_dir=self.dir)
        with open(readme) as fh:
            self.assertNotIn("Program committee, Example Conf", fh.read())


class ExtrasRoundTripTest(unittest.TestCase):
    def test_extras_follow_the_annotations_file(self):
        d = tempfile.mkdtemp()
        cfg = dict(CFG, annotations_path="annotations.json")
        with open(os.path.join(d, "annotations.json"), "w") as fh:
            json.dump({"extras": [{"date": "2026-10-01", "type": "talk", "title": "Public talk"}]}, fh)
        contrib_log.run(FakeGH(), cfg, TODAY, base_dir=d)
        with open(os.path.join(d, "README.md")) as fh:
            self.assertIn("Public talk", fh.read())
        with open(os.path.join(d, "annotations.json"), "w") as fh:
            json.dump({}, fh)
        contrib_log.run(FakeGH(), cfg, TODAY, base_dir=d)
        with open(os.path.join(d, "contributions.json")) as fh:
            self.assertNotIn("Public talk", fh.read())


if __name__ == "__main__":
    unittest.main()
