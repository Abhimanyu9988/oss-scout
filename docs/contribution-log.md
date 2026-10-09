# Contribution log on your profile

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

The digest looks inward: what needs you. The log looks outward: what you've done. It
keeps a section of your profile README (`you/you`) up to date, rebuilt every day from
GitHub's own record, so nothing is logged by hand:

- **PRs you opened,** with opened and merged dates and their component label.
- **Reviews you left on other people's PRs,** each linked to the review itself.
- **Comments you wrote,** each linked to the comment itself rather than just the thread.
- **Issues you opened.**

What visitors see, without clicking anything:

- **A summary:** merged PRs, reviews, comments on others' issues and PRs, issues opened,
  how long you've been active, and the components you've worked in.
- **Merged:** each merged PR with its component, who merged and approved it, its size,
  and the issues it fixed.
- **Recent activity:** your five latest items.
- **Publications and peer review** (optional): from your public ORCID record, with
  citation counts and journal names from OpenAlex.
- **Beyond GitHub:** talks, meetings, program committees and posts you list yourself.
- **Links** to your LinkedIn and ORCID profiles.

Below that, everything else sits in one collapsed block, grouped by repo and then
component, newest first. A full copy, including words of recognition from reviewers, is
kept in `contributions.json`.

How it behaves:

- **Only the marked section changes.** The rest of your README is never touched:

  ```
  <!-- contrib-log:start --> … <!-- contrib-log:end -->
  ```

  If those markers aren't there yet, the section is added at the end.
- **It commits only when something changed.** The output is deterministic, with no
  timestamps that change every day.
- **Daily runs look back 14 days.** Sunday runs, and manual runs with *full* ticked,
  re-read your whole history. That also removes anything you've since deleted.
- **It handles GitHub search's 1,000-result cap** by splitting the date range until each
  slice fits.
- **Replies on your own PRs, and PRs closed without merging, stay in the JSON** but are
  left out of the README.
- **Annotations are optional.** In `annotations.yaml`, add a one-line note keyed by the
  URL of any PR, issue, review or comment, and it appears in the Note column. Tags you
  add stay in the JSON unless you set `readme_show_tags`.
- **Work that happens outside GitHub** goes in an `extras:` list in the same file, and
  appears under "Beyond GitHub":

  ```yaml
  extras:
    - date: 2026-11-24
      type: talk          # talk, meeting, review, post, mentoring, workshop, podcast, other
      title: Kubelet stats in practice
      url: https://example.org/talk
      note: Community day keynote
  ```
- **Permissions:** it reads public data only, and the workflow's write permission covers
  just its own repo.

**Publications and peer reviews from ORCID.** Add `"orcid": "0000-0000-0000-0000"` to
the config. Each run then reads two parts of your public ORCID record, your works and
your peer reviews, and nothing else (not employment, education or other personal
sections). It looks up citation counts and journal names on OpenAlex. Both APIs are
free and need no key. Journals push papers to ORCID automatically once you've linked
them, and services like Web of Science Reviewer Recognition do the same for peer
reviews. If ORCID can't be reached, the previous day's list is kept.

**LinkedIn** doesn't allow automated profile updates or reliable automated posting. So
the log shows a link to your profile (`"links": {"LinkedIn": "https://…"}`), and the
private portfolio writes drafts you can post yourself.

Setup step 8 does all of this for you. By hand:

1. Copy `profile-template/contrib-log.yml` into `you/you` as `.github/workflows/contrib-log.yml`
   and replace `__SCOUT_REPO__` with this repo.
2. Add a `contrib-log.config.json` there with your handle and orgs, for example
   `{"github_user": "you", "orgs": ["open-telemetry"]}`.
3. Run the workflow once with *full* ticked.

| Log setting | Default | Meaning |
|---|---|---|
| `github_user` | required | Whose contributions to log |
| `orgs` / `repos` | at least one | Where to look, e.g. `["open-telemetry"]` |
| `lookback_days` | 14 | How far back daily runs re-check |
| `readme_path` / `data_path` | `README.md` / `contributions.json` | Output files |
| `annotations_path` | `annotations.yaml` | Your notes (`.json` also works) |
| `readme_include_own_comments` | false | Also list replies on your own PRs and issues |
| `readme_hide_closed_prs` | true | Leave PRs closed without merging out of the README |
| `readme_recent_items` | 5 | How many items "Recent activity" shows |
| `readme_show_tags` | false | Show annotation tags in the README |
| `readme_activity_months` | 12 | Months covered by the collapsed "All activity" list; the headline, Merged and the JSON keep everything. 0 shows all |
| `component_label_patterns` | OTel-style label prefixes | Regexes that pick the component label |
| `orcid` | none | Your ORCID iD, for publications and peer reviews |
| `openalex_email` | none | Optional; puts OpenAlex requests in its faster "polite pool" |
| `links` | none | Links to show, e.g. `{"LinkedIn": "https://www.linkedin.com/in/you"}` |

---

[Back to the README](../README.md)
