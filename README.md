# oss-scout

**For people who've started contributing to open source and don't want to drop the ball.**

Finding a first issue is well covered. The [CNCF contributor FAQ](https://contribute.cncf.io/contributors/faq/)
explains how to start, and [CLOTributor](https://clotributor.dev) lists `help wanted` issues across cloud native
projects. Use them.

What comes next is harder to keep track of. Someone opens a PR on the issue you offered to review. Your own PR sits
unreviewed for a week. You said "I'll take this" a month ago. A maintainer replies while you're busy. A good
first issue appears in a project you follow, and someone has claimed it in a comment before you notice.

oss-scout watches all of that for you, every day, and tells you only what changed.

## What you get

One issue in your own repository, kept up to date, that mentions you when something changes:

```
Needs you
  PRs to review on threads you're in
    contrib 51874  migrate semconv  by ThyTran1402 · linked to #51856 (you offered to review)
  Your PRs with no review after 7+ days
    semantic-conventions 4158  Pod memory metrics  open 9d: a polite ping is fine now
  Issues you claimed 14+ days ago with no PR
    contrib 49927  API server node proxy  post an update, or let someone else take it

To pick up
  Free in your areas                          (checked for claim comments and linked PRs)
  Free across open-telemetry, kubernetes, kubernetes-sigs   (good first issue / help wanted)
  Claimed by someone else but quiet for 45+ days

To review
  PRs in your areas waiting 7+ days for a first review
```

Optionally, it also keeps:

- **[A contribution log](docs/contribution-log.md)** on your GitHub profile: merged PRs with who approved them,
  reviews, discussions, and publications from ORCID, rebuilt daily from GitHub's record.
- **[A private portfolio](docs/portfolio.md)** of the same work sorted into your own categories, with
  reviewers' words of recognition and draft LinkedIn posts.

## How it compares

| | CNCF contributor FAQ | CLOTributor | GitHub notifications | oss-scout |
|---|---|---|---|---|
| Finds issues to work on | Advice | `help wanted` issues across CNCF | No | Your components, plus `good first issue` / `help wanted` across chosen organisations |
| Checks for claim comments ("I'd like to work on this", `/assign`) and PRs that mention the issue | No | No | No | Yes |
| Knows about your threads, PRs and claims | No | No | Event by event | As a list of what's waiting on you |
| Reminds you to ping, review or release | Explains when to | No | No | Yes, following the FAQ's timing |
| Records what you've done | No | No | No | Profile log and portfolio |

If you're looking for your very first issue, start with CLOTributor. Come back once you have threads to keep up with.

## Set it up

**In the browser, about 3 minutes:**

1. Click **Use this template → Create a new repository**. Make it public, so the Actions minutes are free.
2. In your copy, open `scout.config.json` and click the pencil icon. Set the repositories and labels you care about,
   and the organisations to look across. Your GitHub handle and repository are picked up automatically.
3. Go to **Actions**, enable workflows if asked, open **Daily digest** and click **Run workflow**.

The first run creates your board and a "watching from today" comment. After that, it runs every weekday morning.

**From a terminal**, for the contribution log and the private portfolio too:

```
git clone https://github.com/Abhimanyu9988/oss-scout
cd oss-scout
./setup.sh
```

Setup asks before every change and can be run again at any time. See [Getting started](docs/getting-started.md).

## Documentation

| Page | |
|---|---|
| [Getting started](docs/getting-started.md) | Both ways to set up, and running it locally |
| [The daily board](docs/board.md) | Every section, how "free" is decided, all settings |
| [Contribution log](docs/contribution-log.md) | Your profile section, notes, talks, ORCID, LinkedIn |
| [Private portfolio](docs/portfolio.md) | Categories, rules, private notes |
| [Changing things later](docs/changing-things.md) | Which files are yours, which are rebuilt, pinning a version |
| [Troubleshooting](docs/troubleshooting.md) | Problems people have hit, and fixes |
| [Contributor's playbook](docs/playbook.md) | Lessons the FAQ doesn't cover |

It reads public data only. The single thing it writes is your board, in your own repository; it never comments
on, claims or opens anything upstream.

## License

Apache 2.0
