# The daily board

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

One issue in your own repository, rebuilt every run. Sections:

**Needs you**

- **PRs to review on threads you're in:** someone opened a PR linked to an issue you opened or commented on, and you
  haven't reviewed it. Drafts are included and marked. If you said you'd review it, the item says so. It stays until
  you review it or the PR closes.
- **Your PRs with no review after 7+ days:** the [CNCF contributor FAQ](https://contribute.cncf.io/contributors/faq/)
  suggests expecting 2 to 7 days, then a polite ping on the PR or in the project's channel.
- **Issues you claimed 14+ days ago with no PR:** post an update, or say you can't get to it so someone else can
  (the FAQ's advice too).

**To pick up**

- **Free in your areas:** open, unassigned issues under the repos and labels you watch, with no linked PR, no claim
  comment and no blocking label.
- **Free across your organisations:** issues labelled `good first issue` or `help wanted` anywhere in the
  organisations you list under `discover`, from the last 90 days, put through the same checks.
- **Claimed by someone else but quiet for 45+ days:** fair to ask, politely, whether you can take over.

**To review**

- **PRs in your areas waiting 7+ days for a first review.** Reviewing these is the fastest way to become useful to
  a project's maintainers.

When something changes, a comment on the board mentions you, so it reaches the GitHub app and your email. It covers
what's new in the sections above, plus comments, reviews, merges and closes on anything you're involved in. Each item
is announced once; quiet days get no comment.

## How it decides an issue is free

GitHub's "linked PR" filter misses a lot, so it reads the timeline of every open,
unassigned issue and looks for three things:

1. **Cross-referenced PRs,** including ones in other repos (such as a semantic-conventions
   PR), and manually linked PRs.
2. **Claim comments** like "I'd like to work on this" or "happy to pick it up", and Kubernetes' `/assign`. The newest
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

## Settings

To watch a whole repository rather than some of its labels, leave `labels` out:
`{"repo": "open-telemetry/opentelemetry-cpp"}`.

To look across whole organisations, add a `discover` block:

```json
"discover": {
  "orgs": ["open-telemetry", "kubernetes", "kubernetes-sigs"],
  "labels": ["good first issue", "help wanted"],
  "languages": ["go"],
  "max_age_days": 90,
  "limit": 30
}
```

`languages` is optional and filters by the repository's main language. Each run re-checks only issues that changed
since the last one, so this stays within GitHub's rate limits.


| Key | Default | Meaning |
|---|---|---|
| `github_user` | required | Your GitHub handle, used for review requests, your activity and your claims |
| `repos` | required | List of `{"repo": "owner/name", "labels": [...]}` |
| `deliver` | `["github"]` | `"github"` (board issue) and/or `"slack"` |
| `board_repo` | the repo the workflow runs in | Where the board issue lives |
| `quiet_days` | 45 | Days without a PR before a claim counts as quiet |
| `stale_review_days` | 7 | Age before an unreviewed PR joins the review list |
| `followup_days` | 60 | How far back to look for issues you're involved in |
| `followup_threads` | 40 | Cap on those issues checked per run |
| `max_issues_per_label` | 60 | Cap on issues read per label |
| `ping_after_days` | 7 | When your own unreviewed PR shows up under Needs you |
| `claim_reminder_days` | 14 | When an issue you claimed without a PR shows up |
| `blocking_labels` | see `scout/classify.py` | Labels that mean the issue is waiting on a decision |
| `board_section_limit` | 25 | Items listed per board section; the rest are counted |
| `board_max_comments` | 100 | After this many comments, the board moves to a fresh issue and the old one is closed with a link |
| `post_when_empty` | false | Comment even on days with nothing new |
| `title` | Contribution scout | Header of the Slack message |

## How often it runs

The digest runs at 06:52 UTC on weekdays. To run it more often, edit the `cron` line in `.github/workflows/digest.yml`. For example, every six hours, every day:

```yaml
    - cron: "52 */6 * * *"
```

It only comments when something changed, so running it more often doesn't add noise. Actions minutes are free for public repos.

---

[Back to the README](../README.md)

## How big it gets

Nothing grows in the repository itself: the board lives in an issue and the run's memory in the Actions cache,
which keeps only the latest copy. Each board section lists at most 25 items, and the "New across your
organisations" comment at most 5. Comments arrive only on days with news, and after 100 the board moves to a new
issue so the page stays quick to load.
