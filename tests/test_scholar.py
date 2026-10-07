import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_contrib_log import CFG, TODAY, USER, FakeGH  # noqa: E402
from scout import contrib_log, portfolio, scholar  # noqa: E402

ORCID = "0009-0005-1179-009X"
WORKS = {"group": [
    {"work-summary": [{"put-code": 1, "title": {"title": {"value": "Detecting memory pressure in Kubernetes pods"}},
                       "type": "journal-article", "journal-title": {"value": "IEEE Access"},
                       "publication-date": {"year": {"value": "2026"}, "month": {"value": "03"}, "day": None},
                       "external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.1109/ACCESS.2026.1234567",
                                                         "external-id-relationship": "self"}]}}]},
    {"work-summary": [{"put-code": 2, "title": {"title": {"value": "Observability at the edge"}}, "type": "conference-paper",
                       "journal-title": None, "publication-date": {"year": {"value": "2025"}},
                       "url": {"value": "https://example.org/paper"}, "external-ids": {"external-id": []}}]},
    {"work-summary": []},
]}
REVIEWS = {"group": [{"peer-review-group": [{"peer-review-summary": [
    {"put-code": 11, "reviewer-role": "reviewer", "review-group-id": "issn:2169-3536",
     "completion-date": {"year": {"value": "2026"}, "month": {"value": "05"}}, "convening-organization": {"name": "IEEE"}},
    {"put-code": 12, "reviewer-role": "reviewer", "review-group-id": "issn:2169-3536",
     "completion-date": {"year": {"value": "2025"}}, "convening-organization": {"name": "IEEE"}},
    {"put-code": 13, "reviewer-role": "reviewer", "review-group-id": "orcid-generated:conf-x",
     "completion-date": {"year": {"value": "2026"}}, "convening-organization": {"name": "Example Conference"}},
]}]}]}


def fake_fetch(calls=None, fail=False):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if fail:
            raise scholar.ScholarError("HTTP 503 for pub.orcid.org")
        if url.endswith("/works"):
            return WORKS
        if url.endswith("/peer-reviews"):
            return REVIEWS
        if "works/doi:10.1109/access.2026.1234567" in url:
            return {"cited_by_count": 7, "primary_location": {"source": {"display_name": "IEEE Access"}}}
        if "sources/issn:2169-3536" in url:
            return {"display_name": "IEEE Access"}
        return None
    return fetch


class ScholarTest(unittest.TestCase):
    def test_collect(self):
        calls = []
        records, errors = scholar.collect({"orcid": ORCID}, fake_fetch(calls))
        self.assertEqual(errors, [])
        papers = [r for r in records if r["type"] == "paper"]
        self.assertEqual([(p["title"], p["date_label"], p["venue"], p.get("citations", 0)) for p in papers], [
            ("Detecting memory pressure in Kubernetes pods", "2026-03", "IEEE Access", 7),
            ("Observability at the edge", "2025", "", 0)])
        self.assertEqual(papers[0]["url"], "https://doi.org/10.1109/access.2026.1234567")
        self.assertEqual(papers[1]["url"], "https://example.org/paper")
        reviews = [r for r in records if r["type"] == "peer_review"]
        self.assertEqual(sorted(r["venue"] for r in reviews), ["Example Conference", "IEEE Access", "IEEE Access"])
        self.assertFalse(any("employment" in u or "education" in u or "person" in u for u in calls))
        self.assertEqual(sum(1 for u in calls if "sources/issn" in u), 1)      # one venue lookup per journal

    def test_no_orcid_and_bad_orcid(self):
        self.assertEqual(scholar.collect({}, fake_fetch()), ([], []))
        records, errors = scholar.collect({"orcid": "12345"}, fake_fetch())
        self.assertEqual(records, [])
        self.assertIn("doesn't look like an ORCID iD", errors[0])

    def test_readme_sections_and_links(self):
        d = tempfile.mkdtemp()
        cfg = dict(CFG, orcid=ORCID, links={"LinkedIn": "https://www.linkedin.com/in/example"})
        contrib_log.run(FakeGH(), cfg, TODAY, base_dir=d, scholar_fetch=fake_fetch())
        with open(os.path.join(d, "README.md")) as fh:
            text = fh.read()
        self.assertIn("2 publications (7 citations) · 3 peer reviews for Example Conference and IEEE Access", text)
        self.assertIn(f"[LinkedIn](https://www.linkedin.com/in/example) · [ORCID](https://orcid.org/{ORCID})", text)
        self.assertIn("### Publications", text)
        self.assertIn("- 2026-03 · [Detecting memory pressure in Kubernetes pods](https://doi.org/10.1109/access.2026.1234567)"
                      " · _IEEE Access_ · cited by 7", text)
        self.assertIn("- **IEEE Access** · 2 reviews (2025–2026)", text)
        self.assertIn("- **Example Conference** · 1 review (2026)", text)
        self.assertIn("**2 merged PRs**", text)                       # research doesn't distort GitHub counts

    def test_orcid_outage_keeps_previous_research(self):
        d = tempfile.mkdtemp()
        cfg = dict(CFG, orcid=ORCID)
        contrib_log.run(FakeGH(), cfg, TODAY, base_dir=d, scholar_fetch=fake_fetch())
        changed, errors, records = contrib_log.run(FakeGH(), cfg, TODAY, base_dir=d, scholar_fetch=fake_fetch(fail=True))
        self.assertTrue(any("ORCID" in e for e in errors))
        self.assertEqual(changed, [])
        self.assertEqual(sum(1 for r in records if r["type"] == "paper"), 2)

    def test_portfolio_research_and_linkedin(self):
        d = tempfile.mkdtemp()
        cfg = dict(CFG, orcid=ORCID)
        contrib_log.run(FakeGH(), cfg, TODAY, base_dir=d, scholar_fetch=fake_fetch())
        notes = os.path.join(d, "notes.json")
        with open(notes, "w") as fh:
            json.dump({"categories": {"publications": "Published research", "reviewing": "Reviewing others' work"},
                       "auto": {"paper": "publications", "peer_review": "reviewing", "review": "reviewing"},
                       "org_names": {"open-telemetry": "OpenTelemetry"}}, fh)
        records, n, user = portfolio.build(os.path.join(d, "contributions.json"), notes)
        text = portfolio.render(records, n, user)
        self.assertIn("| Publications | 2 (7 citations) |", text)
        self.assertIn("| Peer reviews | 3 for 2 venues |", text)
        self.assertIn("- Published 2 peer-reviewed papers in IEEE Access, cited 7 times.", text)
        self.assertIn("- Completed 3 peer reviews for Example Conference and IEEE Access.", text)
        self.assertIn("### Published research (2)", text)
        self.assertIn("- 2026-05 · Peer review · IEEE Access", text)
        self.assertIn("## For LinkedIn", text)
        self.assertIn("My change to `internal/k8sinventory` in opentelemetry-collector-contrib was merged: "
                      "Fix flaky cache sync tests. It closes 2 reported issues. Thanks to TylerHelmuth "
                      "and krisztianfekete for the reviews.", text)
        self.assertIn("I've published 2 papers in IEEE Access and review for Example Conference and IEEE Access.", text)


if __name__ == "__main__":
    unittest.main()
