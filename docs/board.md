# The daily board

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

Tells you which open-source issues and pull requests need you, and stays quiet when
nothing changed.

You list the repositories and labels you care about. Every weekday morning it scans
them and keeps a **Contribution board** issue in your own copy of this repo up to date:

- **PRs to review on threads you're in:** someone opened a PR linked to an issue you
  opened or commented on, and you haven't reviewed it yet. If you said you'd review it,
  the item says so. It stays on the board until you review it or the PR closes.
- **Free to pick up:** no PR, no claim comment, no blocking label.
- **Claimed but quiet:** someone said they'd take it 45+ days ago and never opened a PR.
- **PRs waiting for a first review:** open for 7+ days, and nobody has reviewed them yet.
  Reviewing these is the fastest way to become useful to a project's maintainers.

When something changes, it adds a comment to the board that mentions you, so it reaches
the GitHub app and your email:

- **Needs you:** a review was requested from you; a PR was opened on a thread you're in;
  or someone commented, reviewed, merged or closed an issue or PR you're involved in.
- **New to pick up:** an issue just became free, or a claim just went quiet.
- **Newly waiting for a first review.**

On most days that's zero to three lines. When nothing changed, there's no comment.
Slack is optional, as a second place to receive the same digest.

It reads public data only. The single thing it writes is that board issue, in your own
repo. It never comments on, claims, reviews or opens anything upstream. What you say on
an issue is up to you, in your own words.

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

## Settings

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
| `blocking_labels` | see `scout/classify.py` | Labels that mean the issue is waiting on a decision |
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
