# oss-scout

Tells you which open-source issues and pull requests need you, keeps a public log of what you've done, and stays
quiet when nothing changed.

It runs on GitHub Actions, reads only public data, and never comments on, claims or opens anything upstream.

## What it does

- **[A daily board](docs/board.md):** one issue in your own repo listing PRs to review on threads you're in, issues
  that are really free to pick up, claims that went quiet, and PRs in your areas nobody has reviewed. When something
  changes it mentions you, so it reaches your phone.
- **[A contribution log](docs/contribution-log.md):** an always-current section of your GitHub profile README with
  your merged PRs, reviews, comments, issues, and optionally publications and peer reviews from ORCID.
- **[A private portfolio](docs/portfolio.md):** the same record sorted into your own categories, with reviewers'
  words of recognition, the people you've worked with, CV-ready lines and LinkedIn drafts.

## Set it up in about 5 minutes

```
git clone https://github.com/Abhimanyu9988/oss-scout
cd oss-scout
./setup.sh
```

The guided setup creates your own copy under your account, checks your tools, signs you in to GitHub, runs a test scan, creates your repos and sends the first
digest. It asks before every change, and you can run it again at any time. Details in
[Getting started](docs/getting-started.md).

## Documentation

| Page | What's in it |
|---|---|
| [Getting started](docs/getting-started.md) | Setup, by script or by hand, and running it locally |
| [The daily board](docs/board.md) | What each section means, how "free" is decided, settings, how often it runs |
| [Contribution log](docs/contribution-log.md) | What goes on your profile, annotations, talks, ORCID, LinkedIn |
| [Private portfolio](docs/portfolio.md) | Categories, rules, private notes |
| [Changing things later](docs/changing-things.md) | Which files are yours to edit, which are rebuilt, pinning a version |
| [Troubleshooting](docs/troubleshooting.md) | Problems people have hit, and the fix for each |
| [A contributor's playbook](docs/playbook.md) | Lessons from a first month contributing to OpenTelemetry |

## Roadmap

- Chat commands in Slack (`/scout next`, `/scout check <issue>`)
- Optional thread summaries

## License

Apache 2.0
