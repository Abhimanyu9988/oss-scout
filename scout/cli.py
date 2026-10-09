"""oss-scout: tells you which open-source issues and PRs need you today.

  python3 -m scout digest            # update the board issue (and/or Slack) with today's changes
  python3 -m scout digest --dry-run  # print them instead
  python3 -m scout report            # every bucket, as markdown
  python3 -m scout log               # rebuild your public contribution log
  python3 -m scout portfolio         # build your private, categorised portfolio
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from . import board, contrib_log, portfolio, slack
from .contrib_log import load_log_config
from .digest import build_digest, full_report, scan_candidates
from .github import GitHub, GitHubError, resolve_token
from .state import load_config, load_state, save_state


def main(argv=None, gh=None, now=None, writer=None):
    parser = argparse.ArgumentParser(prog="scout", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=os.environ.get("SCOUT_CONFIG", "scout.config.json"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest", help="post what changed since the last run")
    d.add_argument("--state", default=os.environ.get("SCOUT_STATE", ".scout-state/state.json"))
    d.add_argument("--dry-run", action="store_true", help="print instead of posting; state is still saved")
    d.add_argument("--no-save", action="store_true", help="don't update the state file")
    sub.add_parser("report", help="print every bucket as markdown")
    lg = sub.add_parser("log", help="rebuild your public contribution log (JSON + README section)")
    lg.add_argument("--log-config", default=os.environ.get("SCOUT_LOG_CONFIG", "contrib-log.config.json"))
    lg.add_argument("--full", action="store_true", help="re-read your whole history instead of recent days")
    lg.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    pf = sub.add_parser("portfolio", help="build your private, categorised portfolio from the log's JSON")
    pf.add_argument("--data", default="contributions.json", help="contributions.json from the contribution log")
    pf.add_argument("--notes", default="notes.yaml", help="your private categories, tags and notes")
    pf.add_argument("--out", default="portfolio.md")
    pf.add_argument("--csv", default=None, help="also write a CSV here")
    args = parser.parse_args(argv)

    if args.cmd == "portfolio":
        return run_portfolio(args)

    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    if gh is None:
        token = resolve_token()
        if not token:
            print("warning: no GitHub token found; unauthenticated limits are very low", file=sys.stderr)
        gh = GitHub(token)

    if args.cmd == "log":
        return run_log(args, gh, now)

    cfg = load_config(args.config)

    adopt_fresh_copy(cfg)

    if args.cmd == "report":
        errors = []
        cands = scan_candidates(gh, cfg, now, errors)
        print(full_report(cands))
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 0

    state = load_state(args.state)
    digest, new_state = build_digest(gh, cfg, state, now)

    if digest.errors and not any(digest.counts.values()):
        # Nothing could be read at all (bad token, network, rate limit). Don't post and
        # don't save, or tomorrow's run would report every issue as new.
        for err in digest.errors[:5]:
            print(f"error: {err}", file=sys.stderr)
        print("error: couldn't read anything from GitHub; nothing posted, state unchanged.", file=sys.stderr)
        return 3
    payload = slack.build_payload(digest, cfg.get("title", "Contribution scout"), now.strftime("%a %d %b"))
    text = slack.to_plain_text(payload)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text if not digest.is_empty() else "Nothing new today.\n")

    quiet_day = digest.is_empty() and not cfg.get("post_when_empty", False)
    deliver = cfg.get("deliver") or ["github"]
    unknown = [d for d in deliver if d not in ("github", "slack")]
    if unknown:
        print(f"error: unknown deliver target(s) {unknown}; use 'github' and/or 'slack'", file=sys.stderr)
        return 2
    count = len(digest.needs_you) + len(digest.to_pick) + len(digest.review_queue) + len(digest.discovered)

    if args.dry_run:
        print(text if not quiet_day else "Nothing new today.")
    else:
        # Deliver first; state is saved only if every delivery worked, so a failed
        # day is reported again tomorrow instead of being lost.
        try:
            if "github" in deliver:
                repo = cfg.get("board_repo") or os.environ.get("GITHUB_REPOSITORY", "")
                here = os.environ.get("GITHUB_REPOSITORY", "") if os.environ.get("GITHUB_ACTIONS") else ""
                if not repo:
                    print("error: set board_repo in the config (owner/name of your scout repo)", file=sys.stderr)
                    return 2
                if here and repo.lower() != here.lower() and not os.environ.get("SCOUT_BOARD_TOKEN"):
                    print(f"error: this workflow runs in {here}, but board_repo in scout.config.json is {repo}.\n"
                          f"A workflow can only post to its own repository. If {here} is an old copy, disable this\n"
                          f"workflow (gh workflow disable digest.yml -R {here}); otherwise set board_repo to {here}.",
                          file=sys.stderr)
                    return 2
                if writer is None:
                    writer = GitHub(os.environ.get("SCOUT_BOARD_TOKEN") or os.environ.get("SCOUT_WRITE_TOKEN")
                                    or resolve_token())
                url = board.publish(writer, repo, digest, cfg, now, comment=not quiet_day)
                print(f"Board updated: {url}" + ("" if quiet_day else f" (comment with {count} item(s))"))
            if "slack" in deliver and not quiet_day:
                webhook = os.environ.get("SLACK_WEBHOOK_URL")
                if not webhook:
                    print("error: SLACK_WEBHOOK_URL is not set", file=sys.stderr)
                    return 2
                slack.post(webhook, payload)
                print(f"Posted {count} item(s) to Slack.")
        except (GitHubError, RuntimeError) as err:
            print(f"error: delivery failed, state not saved: {err}", file=sys.stderr)
            return 4
        if quiet_day:
            print("Nothing new today.")

    if not args.no_save:
        save_state(args.state, new_state)
    for err in digest.errors[:3]:
        print(f"error: {err}", file=sys.stderr)
    if len(digest.errors) > 3:
        print(f"error: ...and {len(digest.errors) - 3} more", file=sys.stderr)
    return 0


def adopt_fresh_copy(cfg):
    """A copy made with "Use this template" still carries its author's config. When it runs in
    a repository owned by someone else, use that owner and repository instead."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return False
    here = os.environ.get("GITHUB_REPOSITORY", "")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or here.split("/")[0]
    configured = cfg.get("board_repo") or here
    if not here or not owner or configured.split("/")[0].lower() == owner.lower():
        return False
    print(f"note: this copy belongs to {owner}; using github_user={owner} and board_repo={here}. "
          "Edit scout.config.json to choose your own projects.")
    cfg["github_user"], cfg["board_repo"] = owner, here
    return True


def run_portfolio(args):
    records, notes, user = portfolio.build(args.data, args.notes)
    changed = [p for p, text in ((args.out, portfolio.render(records, notes, user)),
                                 (args.csv, portfolio.to_csv(records) if args.csv else None))
               if p and portfolio.write_if_changed(p, text)]
    print(f"{len(records)} items in the portfolio.")
    print(f"Changed: {', '.join(changed)}" if changed else "No changes.")
    return 0


def run_log(args, gh, now):
    cfg = load_log_config(args.log_config)
    changed, errors, records = contrib_log.run(gh, cfg, now.date(), full=args.full, dry_run=args.dry_run)
    c = contrib_log.summary_counts(records)
    pl = contrib_log._plural
    print(f"{len(records)} contributions: {pl(c['merged'], 'merged PR')}, {pl(c['reviews'], 'review')}, "
          f"{pl(c['comments'], 'comment')} on others' work, {pl(c['issues'], 'issue')} opened")
    verb = "Would change" if args.dry_run else "Changed"
    print(f"{verb}: {', '.join(changed)}" if changed else "No changes.")
    for err in errors[:3]:
        print(f"error: {err}", file=sys.stderr)
    if len(errors) > 3:
        print(f"error: ...and {len(errors) - 3} more", file=sys.stderr)
    return 3 if errors and not changed else 0


if __name__ == "__main__":
    sys.exit(main())
