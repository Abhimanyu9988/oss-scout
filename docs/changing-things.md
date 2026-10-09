# Changing things later

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

Every part separates what's rebuilt daily from what's yours. Your files are never
overwritten:

| Repo | Rebuilt daily (don't hand-edit) | Yours to edit |
|---|---|---|
| your copy of oss-scout | the board issue | `scout.config.json`: repos and labels to watch, delivery |
| your profile, `you/you` | the marked log section, `contributions.json` | the rest of your README, `contrib-log.config.json`, `annotations.yaml` |
| your private portfolio | `portfolio.md`, `portfolio.csv` | `notes.yaml` |

To change something:

1. **Edit the file on github.com** (pencil icon), or locally and push.
2. **Wait for the next daily run,** or start one now: *Actions*, pick the workflow, *Run
   workflow*. On the profile repo, tick *full* to re-read your whole history.

Running `./setup.sh` again also works. It skips whatever is already done and lets you
change answers such as your intro line, ORCID iD or LinkedIn link.

## Using a fixed version

The profile and portfolio workflows fetch this repo's code on every run (`ref: main` in
their workflow files). That's convenient for the author. If you're using someone else's
copy, pin a release so their later changes can't surprise you:

```yaml
        with:
          repository: Abhimanyu9988/oss-scout
          ref: v0.3.0
```

## Good to know

- **The run's memory is kept in the Actions cache,** not in git. This is the record of what
  it already told you. If the cache is evicted (GitHub drops caches unused for 7 days), the
  next run simply posts a new baseline.
- **Failed days aren't lost.** If posting fails, nothing is marked as seen, so the next run
  reports it again.
- **GitHub turns off scheduled workflows in public repos after 60 days without commits.**
  If the comments stop, re-enable the workflow from the Actions tab.

---

[Back to the README](../README.md)
