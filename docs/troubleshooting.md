# Troubleshooting

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

Problems people have actually hit, and the fix for each.

## A daily run failed with "A workflow can only post to its own repository"

The workflow runs in one repo but `board_repo` in `scout.config.json` names another. This usually means an old
copy of oss-scout still has its schedule switched on. Switch it off, or delete the old copy:

```
gh workflow disable digest.yml -R you/old-copy
```

Setup does this for you when it creates an archive, and on any later run.

## "You have exceeded a secondary rate limit"

GitHub limits how fast its search API can be called. oss-scout makes at most a few searches per run and waits a
minute and retries when it's told to slow down. If you see this right after running setup several times in a row,
wait a few minutes and run it again.

## The test scan says "Bad credentials" or reads nothing

`gh` isn't signed in, or is signed in as a different account. Run `gh auth status`. Setup step 2 checks this and
offers to sign you in as the account in `scout.config.json`.

## `gh repo delete` says it needs the "delete_repo" scope

Deleting a repo needs an extra permission that `gh` doesn't ask for by default:

```
gh auth refresh -h github.com -s delete_repo
```

You can remove it again afterwards with `gh auth refresh -h github.com -r delete_repo`.

## Scheduled runs stopped

GitHub switches off scheduled workflows in public repos after 60 days without a commit. Re-enable the workflow
from the Actions tab.

## The digest reported everything as new again

Its memory of what it already told you is kept in the Actions cache, which GitHub drops after 7 days without use.
The next run then posts a fresh baseline. Nothing is lost.

## My papers don't appear in the contribution log

They're read from your public ORCID record. Check that each paper is on it, and linked with its DOI: on orcid.org
use *Add works → Search & link*. Peer reviews appear only if a journal or a service such as Web of Science Reviewer
Recognition added them to ORCID; list others under `extras:` instead.

## A notice says Node.js 20 is deprecated

Update to the latest oss-scout. Its workflows use action versions that run on Node 24.

## Something else

Open an issue on this repo with the job log from the Actions tab.

---

[Back to the README](../README.md)
