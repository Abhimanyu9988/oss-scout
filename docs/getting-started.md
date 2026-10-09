# Getting started

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

## In the browser

1. Click **Use this template → Create a new repository** on
   [Abhimanyu9988/oss-scout](https://github.com/Abhimanyu9988/oss-scout). Make it public, so the Actions minutes
   are free.
2. In your copy, open `scout.config.json`, click the pencil icon, and choose what to watch:
   - `repos`: repositories, each with the labels you care about (or no labels for the whole repository);
   - `discover.orgs`: organisations to look across for `good first issue` and `help wanted` issues.

   Your GitHub handle and repository are picked up automatically, so you don't need to change `github_user` or
   `board_repo`.
3. Go to **Actions**, enable workflows if asked, open **Daily digest**, and click **Run workflow**.

The first run creates your board and posts a "watching from today" comment. Watch the repository, or install the
GitHub mobile app, to get the comments as notifications.

## From a terminal

The terminal setup does the same, and can also add the contribution log to your profile and the private portfolio.

You need a Mac or Linux machine with Python 3.9+, git and the [GitHub CLI](https://cli.github.com/). Setup offers to
install whatever is missing with Homebrew.

```
git clone https://github.com/Abhimanyu9988/oss-scout
cd oss-scout
./setup.sh
```

Setup notices it's a fresh clone, creates your own copy under your account, and switches the config to your GitHub
handle. Edit `scout.config.json` afterwards to choose the repos and labels you care about (see
[the board's settings](board.md#settings)).

The guided setup:
1. Checks Python and the GitHub CLI.
2. Makes sure you're signed in as the account in `scout.config.json`.
3. Runs a test scan on real data.
4. Creates your repo.
5. Asks whether you also want Slack.
6. Offers to make the repo public.
7. Sends the first digest and opens your board.
8. Offers to add a [contribution log](contribution-log.md) to your GitHub profile, with an
   optional intro line.
9. Offers a [private portfolio](portfolio.md) repo.

It asks before every change. Run it again at any time to finish, update or push changes
to your config.

Want it on your phone? Install the GitHub mobile app and allow notifications.

## Or by hand

1. **Fork or copy this repo** to your account and edit `scout.config.json`: your GitHub handle,
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

## Run it locally

Python 3.9+ and nothing to install. It uses `GITHUB_TOKEN` or `gh auth token`.

```
python3 -m scout digest --dry-run --no-save    # print today's digest, change nothing
python3 -m scout report                        # every bucket, as markdown
python3 -m scout log --dry-run                 # what the contribution log would change
python3 -m scout portfolio --notes notes.yaml   # build the private portfolio
python3 -m unittest discover -s tests
```

---

[Back to the README](../README.md)
