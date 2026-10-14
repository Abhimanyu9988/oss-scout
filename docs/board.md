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
- **Waiting on others:** open threads where you had the last word 7+ days ago and nobody has replied since: your
  comment, an issue you opened, fixes you pushed after a review, or a re-review you asked for. Approving someone
  else's PR doesn't count, and items already listed above aren't repeated. A short, polite nudge is fine.

**To triage**

- **Opened in the last 7 days in your areas:** every open issue opened recently under your repos and labels,
  whatever state it's in, including ones claimed in their first comment or labelled as waiting the day they were
  opened. "Free in your areas" only shows issues nobody has claimed, so it was never a list of everything new. Each
  item says who has replied (bots and the reporter don't count), whether it still has `needs triage`, and when an
  issue was matched by keyword because it has **no component label**. When someone already has an open PR for
  the issue, the item says so (`PR #16168 open by …`, or `a PR is linked` when it was linked by hand), and it no
  longer counts as unanswered. Issues nobody has answered and nobody is fixing come first.

  This is the queue to work from if you want to become a triager: answer new issues with the clarifying questions
  a maintainer would ask, reproduce bugs, and point out duplicates. Most projects ask for about a month of that
  before a triager nomination.

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

When a new section appears after an upgrade, the first run records what's in it without announcing it, so you don't
get a flood of old items as "new".

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

To set up triage, add a `triage` block:

```json
"triage": {
  "days": 7,
  "keywords": ["kubelet", "k8s", "sqlserver"],
  "repos": [
    {"repo": "open-telemetry/opentelemetry-collector"},
    {"repo": "open-telemetry/semantic-conventions", "keywords": ["k8s", "pod"]}
  ]
}
```

- `keywords` catch issues in your watched repos that were filed without a component label: a new issue whose title
  or labels contain one of them is listed even if it has none of your labels. Matching is case-insensitive and
  matches the start of a word, so `k8s` also matches `k8scluster`.
- `repos` are watched for new issues only, not for free issues or PRs. Leave out `keywords` (and `labels`) to see
  every new issue in that repo, or add them to narrow it down.
- Set `"enabled": false` to turn triage off.

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
| `activity_threads` | 200 | Cap on threads checked for new activity since the last run, newest first; a run that hits it says so in a Partial scan note |
| `max_issues_per_label` | 60 | Cap on issues read per label |
| `ping_after_days` | 7 | When your own unreviewed PR shows up under Needs you |
| `claim_reminder_days` | 14 | When an issue you claimed without a PR shows up |
| `waiting_days` | 7 | How long after your last word, with no reply, a thread shows under Waiting on others |
| `waiting_threads` | 50 | Cap on threads checked for Waiting on others per run |
| `triage.days` | 7 | How far back the triage list goes |
| `triage.keywords` | none | Words that catch unlabelled new issues in your watched repos |
| `triage.repos` | none | Extra repos watched for new issues only |
| `triage.max_per_repo` | 300 | Cap on new issues read per repo |
| `triage.max_checks` | 150 | Cap on issues whose comments and linked PRs are read per run; the rest are read on later runs |
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
