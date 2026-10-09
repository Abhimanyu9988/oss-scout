# Private portfolio

[Getting started](getting-started.md) · [The board](board.md) · [Contribution log](contribution-log.md) · [Private portfolio](portfolio.md) · [Changing things](changing-things.md) · [Troubleshooting](troubleshooting.md) · [Contributor's playbook](playbook.md)

Your public profile shows the work. A portfolio explains it, in your own categories,
for a promotion case, a CV, a fellowship or any application. `scout portfolio` builds
one from the log's `contributions.json` and a notes file you keep private. Setup step 9
puts both in a private repo that rebuilds `portfolio.md` and `portfolio.csv` every day.
The portfolio contains:

- **At a glance:** merged PRs and lines changed, reviews of others' work, discussions,
  repositories and components, the maintainers who approved or merged your work, and
  words of recognition.
- **CV-ready lines** you can paste.
- **Every item filed under your categories,** with its evidence: size, approvals, who
  merged it, what it fixed, and what reviewers said about it.
- **Recognition from peers:** the reviewers' comments that praised your work, with links.
  Plain "thanks" and review requests don't count; it quotes the sentence that does the
  praising.
- **People you've worked with:** who merged, approved or praised your work, and whose PRs
  you reviewed. Useful when you need someone who knows your work first-hand.
- **Publications and peer reviews** from ORCID, if configured.
- **Timeline:** month by month.
- **For LinkedIn:** a ready-to-edit About paragraph, and draft posts for PRs merged in the
  last 30 days and papers from the last 90.
- **Not yet in a category:** what's still unfiled.

Everything is configured in `notes.yaml` (see `portfolio-template/notes.yaml`):

- **`categories`:** your headings, in your order.
- **`auto`:** which category each kind of contribution goes to by default, for example
  `pr_merged: original-work`.
- **`items`:** extra categories and private notes for specific URLs.
- **`extras`:** things that belong in the portfolio but never on your public profile.

```
python3 -m scout portfolio --data contributions.json --notes notes.yaml --out portfolio.md --csv portfolio.csv
```

---

[Back to the README](../README.md)
