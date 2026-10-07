# oss-scout

Tells you which open-source issues and pull requests need you, and stays quiet when
nothing changed.

You list the repositories and labels you care about. Every weekday morning it scans
them and keeps a **Contribution board** issue in your own copy of this repo up to date:

- **Free to pick up:** no PR, no claim comment, no blocking label.
- **Claimed but quiet:** someone said they'd take it 45+ days ago and never opened a PR.
- **PRs waiting for a first review:** open for 7+ days, and nobody has reviewed them yet.
  Reviewing these is the fastest way to become useful to a project's maintainers.

When something changes, it adds a comment to the board that mentions you, so it reaches
the GitHub app and your email:

- **Needs you:** a review was requested from you, or someone commented, reviewed, merged or
  closed an issue or PR you're involved in.
- **New to pick up:** an issue just became free, or a claim just went quiet.
- **Newly waiting for a first review.**

On most days that's zero to three lines. When nothing changed, there's no comment.
Slack is optional, as a second place to receive the same digest.

It reads public data only. The single thing it writes is that board issue, in your own
repo. It never comments on, claims, reviews or opens anything upstream. What you say on
an issue is up to you, in your own words.

## Set it up (about 5 minutes)

```
./setup.sh
```

The guided setup:
1. Checks Python and the GitHub CLI.
2. Makes sure you're signed in as the account in `scout.config.json`.
3. Runs a test scan on real data.
4. Creates your repo.
5. Asks whether you also want Slack.
6. Offers to make the repo public.
7. Sends the first digest and opens your board.
8. Offers to add a contribution log to your GitHub profile (see below).

It asks before every change. Run it again at any time to finish, update or push changes
to your config.

Want it on your phone? Install the GitHub mobile app and allow notifications.

### Or by hand

1. **Copy this repo** to your account and edit `scout.config.json`: your GitHub handle,
   `board_repo` (your copy, `owner/name`), and the repos and labels to watch. Label names
   must match the repo's labels exactly; check each repo's *Issues → Labels* page.
2. **Run the workflow once.** Go to *Actions → Daily digest → Run workflow*. The first run
   creates the board and posts a short "watching from today" comment. From then on you
   only hear about changes.
3. **Optional Slack.** Add `"slack"` to `deliver`. Create an incoming webhook in a
   workspace you control and save it as the repo secret `SLACK_WEBHOOK_URL`.
   Community workspaces like CNCF's generally don't allow personal apps.

It runs at 06:52 UTC on weekdays. To change that, edit the `cron` line in
`.github/workflows/digest.yml`.

## How it decides an issue is free

GitHub's "linked PR" filter misses a lot, so it reads the timeline of every open,
unassigned issue and looks for three things:

1. **Cross-referenced PRs,** including ones in other repos (such as a semantic-conventions
   PR), and manually linked PRs.
2. **Claim comments** like "I'd like to work on this" or "happy to pick it up". The newest
   claim decides the bucket:
   - recent means *taken*
   - 45+ days with no PR means *claimed but quiet*
   - your own claim means *yours*
3. **Blocking labels:** waiting for author, code owners or semantic conventions, or
   discussion needed.

Claim detection is a heuristic. Read the thread before you comment.

## Sharing the board

If you make your repo public, the board is a live list of open work that anyone can use.
It's built so that this doesn't bother upstream maintainers:

- **No backlinks.** Links go through `redirect.github.com`, as Dependabot's do, so the board
  doesn't leave a "mentioned this issue" note on every issue it lists.
- **No pings.** Any `@handle` in an issue title is neutralised, so the board never notifies
  anyone except you.

Others can also copy the repo and point it at their own areas.

## Contribution log on your profile

The digest looks inward: what needs you. The log looks outward: what you've done. It
keeps a section of your profile README (`you/you`) up to date, rebuilt every day from
GitHub's own record, so nothing is logged by hand:

- **PRs you opened,** with opened and merged dates and their component label.
- **Reviews you left on other people's PRs,** each linked to the review itself.
- **Comments you wrote,** each linked to the comment itself rather than just the thread.
- **Issues you opened.**

The section starts with a summary: merged PRs, reviews, comments on others' issues and
PRs, issues opened, and the components you've worked in. Underneath, items are grouped
by repo, then by component, newest first. A full copy is kept in `contributions.json`.

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
- **Replies on your own PRs stay in the JSON** but are left out of the README.
- **Annotations are optional.** In `annotations.yaml`, add a one-line note keyed by the
  URL of any PR, issue, review or comment, and it appears in the Note column. Tags you
  add stay in the JSON unless you set `readme_show_tags`.
- **Permissions:** it reads public data only, and the workflow's write permission covers
  just its own repo.

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
| `readme_show_tags` | false | Show annotation tags in the README |
| `component_label_patterns` | OTel-style label prefixes | Regexes that pick the component label |

## Run it locally

Python 3.9+ and nothing to install. It uses `GITHUB_TOKEN` or `gh auth token`.

```
python3 -m scout digest --dry-run --no-save    # print today's digest, change nothing
python3 -m scout report                        # every bucket, as markdown
python3 -m scout log --dry-run                 # what the contribution log would change
python3 -m unittest discover -s tests
```

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `github_user` | required | Your GitHub handle, used for review requests, your activity and your claims |
| `repos` | required | List of `{"repo": "owner/name", "labels": [...]}` |
| `deliver` | `["github"]` | `"github"` (board issue) and/or `"slack"` |
| `board_repo` | the repo the workflow runs in | Where the board issue lives |
| `quiet_days` | 45 | Days without a PR before a claim counts as quiet |
| `stale_review_days` | 7 | Age before an unreviewed PR joins the review list |
| `max_issues_per_label` | 60 | Cap on issues read per label |
| `blocking_labels` | see `scout/classify.py` | Labels that mean the issue is waiting on a decision |
| `post_when_empty` | false | Comment even on days with nothing new |
| `title` | Contribution scout | Header of the Slack message |

## Good to know

- **The run's memory is kept in the Actions cache,** not in git. This is the record of what
  it already told you. If the cache is evicted (GitHub drops caches unused for 7 days), the
  next run simply posts a new baseline.
- **Failed days aren't lost.** If posting fails, nothing is marked as seen, so the next run
  reports it again.
- **GitHub turns off scheduled workflows in public repos after 60 days without commits.**
  If the comments stop, re-enable the workflow from the Actions tab.

## Roadmap

- Hide board items you've already responded to
- Chat commands in Slack (`/scout next`, `/scout check <issue>`)
- Optional thread summaries

## License

Apache 2.0
