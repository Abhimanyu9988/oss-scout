#!/usr/bin/env bash
# oss-scout guided setup. Safe to run again: every step checks what's already done
# and asks before changing anything. Works with the bash that ships with macOS (3.2).

set -uo pipefail

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
CONFIG="$ROOT/scout.config.json"
STEPS=9

if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi

step()  { printf '\n%s━━ Step %s of %s: %s%s\n' "$B" "$1" "$STEPS" "$2" "$N"; }
ok()    { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
warn()  { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
fail()  { printf '%s✗ %s%s\n' "$R" "$*" "$N"; exit 1; }
info()  { printf '  %s\n' "$*"; }
pause() { printf '\n%s→ %s%s ' "$B" "${1:-Press Enter to continue}" "$N"; read -r _; }

ask() {   # ask "Question" [Y|N]  -> returns 0 for yes
  local default="${2:-Y}" hint="[Y/n]" reply
  [ "$default" = "N" ] && hint="[y/N]"
  printf '%s? %s %s%s ' "$B" "$1" "$hint" "$N"
  read -r reply
  reply="$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')"
  [ -z "$reply" ] && reply="$(printf '%s' "$default" | tr '[:upper:]' '[:lower:]')"
  [ "$reply" = "y" ] || [ "$reply" = "yes" ]
}

prompt() {  # prompt "Question" "default" -> echoes answer
  local reply
  printf '%s? %s%s %s[%s]%s ' "$B" "$1" "$N" "$D" "$2" "$N" >&2
  read -r reply
  printf '%s' "${reply:-$2}"
}

open_url() {
  info "Opening: $1"
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1
  else info "(open it in your browser)"; fi
}

config_user() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["github_user"])' "$CONFIG"; }

printf '%soss-scout setup%s\n' "$B" "$N"
info "About 10 minutes. Each step explains itself and asks before it changes anything."
info "Press Ctrl+C at any point; running ./setup.sh again picks up where you left off."

# ───────────────────────────────────────────────────────────── 1
step 1 "Check the tools on this Mac"

if python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
else
  warn "Python 3.9 or newer is needed."
  if command -v brew >/dev/null 2>&1 && ask "Install it with Homebrew now?"; then
    brew install python || fail "Homebrew couldn't install Python."
  else
    open_url "https://www.python.org/downloads/macos/"
    fail "Install Python, then run ./setup.sh again."
  fi
fi

if ! command -v gh >/dev/null 2>&1; then
  warn "The GitHub CLI (gh) isn't installed. It creates the repo and stores the Slack secret."
  if command -v brew >/dev/null 2>&1 && ask "Install it with Homebrew now?"; then
    brew install gh || fail "Homebrew couldn't install gh."
  else
    open_url "https://cli.github.com/"
    fail "Install gh, then run ./setup.sh again."
  fi
fi
ok "GitHub CLI $(gh --version | head -1 | awk '{print $3}')"
command -v git >/dev/null 2>&1 || fail "git is missing. Run: xcode-select --install"
ok "git"

# ───────────────────────────────────────────────────────────── 2
step 2 "Sign in to GitHub as the right account"

WANT="$(config_user)"
if ! gh auth status >/dev/null 2>&1; then
  info "gh will open your browser to sign in. Choose GitHub.com, HTTPS, and log in with a web browser."
  pause "Press Enter to start sign-in"
  gh auth login --hostname github.com --git-protocol https --web --scopes workflow || fail "Sign-in didn't finish."
fi
HAVE="$(gh api user --jq .login 2>/dev/null || true)"
if [ "$(printf '%s' "$HAVE" | tr '[:upper:]' '[:lower:]')" != "$(printf '%s' "$WANT" | tr '[:upper:]' '[:lower:]')" ]; then
  warn "gh is signed in as '${HAVE:-nobody}', but scout.config.json says '$WANT'."
  info "The repo and the daily job belong to that account, so sign in as $WANT."
  if ask "Sign in again as $WANT now?"; then
    gh auth login --hostname github.com --git-protocol https --web --scopes workflow || fail "Sign-in didn't finish."
    HAVE="$(gh api user --jq .login)"
    [ "$(printf '%s' "$HAVE" | tr '[:upper:]' '[:lower:]')" = "$(printf '%s' "$WANT" | tr '[:upper:]' '[:lower:]')" ] \
      || fail "Still signed in as $HAVE. If you have both accounts, run: gh auth switch"
  else
    fail "Change github_user in scout.config.json or sign in as $WANT, then run ./setup.sh again."
  fi
fi
ok "Signed in as $HAVE"

if ! gh auth status 2>&1 | grep -qi "workflow"; then
  info "Pushing the scheduled workflow file needs one more GitHub permission (workflow)."
  pause "Press Enter to approve it in your browser"
  gh auth refresh --hostname github.com --scopes workflow || fail "Couldn't add the workflow permission."
fi
gh auth setup-git >/dev/null 2>&1 && ok "git will use this account when pushing"

# Already set up on an earlier run? Then the slow test scan is optional.
REPO=""
if git rev-parse --git-dir >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
fi

push_changes() {   # push_changes "commit message"
  if [ -n "$(git status --porcelain)" ]; then
    git add -A && git commit -qm "$1" && git push -q && ok "Pushed to https://github.com/$REPO"
  fi
}

# ───────────────────────────────────────────────────────────── 3
step 3 "Try it on real GitHub data"

RUN_SCAN=1
if [ -n "$REPO" ]; then
  info "You ran this scan on an earlier setup run."
  ask "Run it again?" N || RUN_SCAN=0
fi
if [ "$RUN_SCAN" = "1" ]; then
  info "This reads your configured issues and PRs and prints what the digest would say."
  info "It posts nothing and doesn't remember anything yet. It takes 1–3 minutes."
  pause "Press Enter to run it"
  OUT="$(mktemp)"
  python3 -m scout digest --dry-run --no-save 2>&1 | tee "$OUT"
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    fail "The scan failed. Copy the output above into the chat."
  elif grep -q "Partial scan" "$OUT"; then
    warn "The scan mostly worked, but some requests failed (see the errors above)."
    info "Answer n and paste the output into the chat if they don't look like a one-off."
  else
    ok "The scan worked with no errors"
  fi
  rm -f "$OUT"
  ask "Does that output look reasonable?" || fail "Paste the output into the chat and we'll fix it first."
else
  ok "Skipped"
fi

# ───────────────────────────────────────────────────────────── 4
step 4 "Put it in your own GitHub repo"

if [ -n "$REPO" ]; then
  ok "Already connected to https://github.com/$REPO"
  if [ -n "$(git status --porcelain)" ]; then
    info "This folder has newer files than the repo (for example an updated version of the tool)."
    ask "Push them?" && push_changes "Update oss-scout"
  fi
else
  info "The repo holds the code and runs the daily job on GitHub Actions, so your laptop can be off."
  info "Private is the safe default; you can make it public later from the repo's settings."
  NAME="$(prompt "Repository name" "oss-scout")"
  VIS="--private"
  ask "Make it public now?" N && VIS="--public"

  info "Commits in this repo are public and show the email they're made with."
  EMAIL=""
  while :; do
    EMAIL="$(prompt "Email for commits in this repo" "$(git config --get user.email || true)")"
    case "$EMAIL" in *@*.*) break ;; *) warn "Enter an email address." ;; esac
  done
  [ -d .git ] || git init -q -b main
  git config user.email "$EMAIL"
  git config user.name "$(git config --get user.name || prompt "Your name for commits" "$HAVE")"
  git add -A
  git commit -qm "oss-scout: daily digest of issues and PRs that need you" || true
  gh repo create "$NAME" $VIS --source . --remote origin --push \
    --description "Daily digest of open-source issues and PRs that need you" \
    || fail "Creating the repo failed. If '$NAME' already exists, run again with another name."
  REPO="$HAVE/$NAME"
  ok "Created https://github.com/$REPO"
fi

# ───────────────────────────────────────────────────────────── 5
step 5 "Choose where the digest goes"

info "GitHub (always on, nothing to set up): a 'Contribution board' issue in $REPO."
info "  The issue always shows everything free, quiet or waiting for review. When something"
info "  changes you get a comment mentioning you, so it reaches the GitHub app and your email."
DELIVER='["github"]'

slack_connected() { gh secret list -R "$REPO" 2>/dev/null | grep -q '^SLACK_WEBHOOK_URL'; }

WANT_SLACK=0
if slack_connected; then
  ok "Slack is also connected"
  ask "Keep sending to Slack too?" && WANT_SLACK=1
  [ "$WANT_SLACK" = "1" ] && ask "Replace the Slack webhook?" N && NEED_HOOK=1 || NEED_HOOK=0
else
  ask "Also send it to Slack? (optional)" N && WANT_SLACK=1
  NEED_HOOK=$WANT_SLACK
fi

if [ "$NEED_HOOK" = "1" ]; then
  if ! ask "Do you already have a personal Slack workspace (not a work one)?" N; then
    info "Slack doesn't let any tool create a workspace for you, so this part is by hand:"
    info "  sign up with your personal email, enter the code they send, name the workspace,"
    info "  and create a channel such as #oss-scout."
    pause "Press Enter to open Slack sign-up"
    open_url "https://slack.com/get-started#/createnew"
    pause "Press Enter once your workspace and channel exist"
  fi
  MANIFEST='{"display_information":{"name":"oss-scout","description":"Daily digest of open-source issues and PRs that need you"},"oauth_config":{"scopes":{"bot":["incoming-webhook"]}},"settings":{"org_deploy_enabled":false,"socket_mode_enabled":false,"token_rotation_enabled":false}}'
  ENC="$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$MANIFEST")"
  info "Next I'll open Slack with the app already filled in. Then:"
  info "  1. Pick your personal workspace, click Next, then Create"
  info "  2. In the left sidebar open 'Install App', click 'Install to <workspace>'"
  info "  3. Choose the channel, click Allow"
  info "  4. In the left sidebar open 'Incoming Webhooks' and copy the URL at the bottom"
  info "     (it starts with https://hooks.slack.com/services/)"
  pause "Press Enter to open Slack"
  open_url "https://api.slack.com/apps?new_app=1&manifest_json=$ENC"
  info "If the form isn't filled in, choose 'Create New App → From scratch' and add Incoming Webhooks."

  while :; do
    printf '\n%s? Paste the webhook URL (hidden, nothing shows while you paste)%s ' "$B" "$N"
    read -rs HOOK; echo
    case "$HOOK" in
      https://hooks.slack.com/*) break ;;
      *) warn "That doesn't look like a Slack webhook URL. Try again." ;;
    esac
  done

  info "Sending a test message..."
  RESP="$(curl -s -X POST -H 'Content-Type: application/json' \
    --data '{"text":"oss-scout is connected. Your first digest is on its way."}' "$HOOK")"
  [ "$RESP" = "ok" ] || fail "Slack replied: $RESP. Check the URL and run ./setup.sh again."
  ask "Did the test message arrive in your channel?" || fail "Check which channel you picked in Slack, then run ./setup.sh again."
  printf '%s' "$HOOK" | gh secret set SLACK_WEBHOOK_URL -R "$REPO" || fail "Couldn't save the secret."
  unset HOOK
  ok "Webhook saved as a GitHub secret (it isn't stored on this Mac)"
fi
[ "$WANT_SLACK" = "1" ] && DELIVER='["github", "slack"]'

python3 - "$CONFIG" "$DELIVER" "$REPO" <<'PY'
import json, sys
path, deliver, repo = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
with open(path) as fh:
    cfg = json.load(fh)
if cfg.get("deliver") != deliver or cfg.get("board_repo") != repo:
    cfg["deliver"], cfg["board_repo"] = deliver, repo
    with open(path, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
PY
push_changes "Set digest delivery: $(printf '%s' "$DELIVER" | tr -d '[]"')"
ok "Delivery: $(printf '%s' "$DELIVER" | tr -d '[]"')"

run_and_watch() {   # run_and_watch owner/repo workflow.yml [extra gh workflow run args...]
  local repo="$1" wf="$2" start run_id=""
  shift 2
  gh workflow enable "$wf" -R "$repo" >/dev/null 2>&1 || true
  start="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for _ in 1 2 3 4 5 6; do
    gh workflow run "$wf" -R "$repo" "$@" >/dev/null 2>&1 && break
    sleep 5   # a just-pushed workflow can take a moment to register
  done
  info "Waiting for GitHub to start the run..."
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 5
    run_id="$(gh run list -R "$repo" --workflow "$wf" --limit 5 --json databaseId,createdAt \
      --jq "[.[] | select(.createdAt >= \"$start\")][0].databaseId // empty" 2>/dev/null || true)"
    [ -n "$run_id" ] && break
  done
  if [ -z "$run_id" ]; then
    warn "The run hasn't shown up yet. Opening the Actions page so you can watch it."
    open_url "https://github.com/$repo/actions/workflows/$wf"
    return 2
  fi
  if gh run watch "$run_id" -R "$repo" --exit-status; then
    return 0
  fi
  warn "The run failed. Opening its log; paste the error into the chat."
  open_url "https://github.com/$repo/actions/runs/$run_id"
  return 1
}

# ───────────────────────────────────────────────────────────── 6
step 6 "Make it public (optional)"

if gh repo view "$REPO-archive" >/dev/null 2>&1 \
   && [ "$(gh workflow view digest.yml -R "$REPO-archive" --json state --jq .state 2>/dev/null)" = "active" ]; then
  gh workflow disable digest.yml -R "$REPO-archive" >/dev/null 2>&1 \
    && ok "Switched off the daily run in the old copy, $REPO-archive"
fi
VISIBILITY="$(gh repo view "$REPO" --json visibility --jq .visibility 2>/dev/null || echo UNKNOWN)"
if [ "$VISIBILITY" = "PUBLIC" ]; then
  ok "$REPO is public"
else
  info "Public lets others browse your board and use the tool, and lets the contribution"
  info "log in step 8 run without any extra token. Everything it shows is already public data."
  if ask "Make $REPO public?"; then
    COMMITS="$(git rev-list --count HEAD 2>/dev/null || echo 0)"
    RUNS="$(gh run list -R "$REPO" --limit 1 --json databaseId --jq length 2>/dev/null || echo 0)"
    if [ "$COMMITS" -le 1 ] && [ "$RUNS" = "0" ]; then
      gh repo edit "$REPO" --visibility public --accept-visibility-change-consequences >/dev/null \
        || fail "Couldn't change the visibility."
      ok "$REPO is now public"
    else
      NAME="${REPO##*/}"
      ARCHIVE="$NAME-archive"
      info "Switching this repo to public would also publish its earlier commits and Actions logs."
      info "Safer: keep this repo private as $ARCHIVE (delete it whenever you like) and create"
      info "a new public $NAME with a single clean commit of the current files."
      info "The board issue starts fresh there with a new 'watching from today' comment."
      if ask "Go ahead?"; then
        gh repo rename "$ARCHIVE" -R "$REPO" --yes >/dev/null || fail "Couldn't rename the repo."
        gh workflow disable digest.yml -R "$HAVE/$ARCHIVE" >/dev/null 2>&1 || true
        ok "Old repo kept, private, with its daily run switched off: https://github.com/$HAVE/$ARCHIVE"
        git checkout -q --orphan oss-scout-public
        git add -A
        git commit -qm "oss-scout: a daily digest of open-source work that needs you"
        git branch -D main >/dev/null 2>&1 || true
        git branch -m main
        git remote remove origin
        gh repo create "$REPO" --public --source . --remote origin --push \
          --description "Daily digest of open-source issues and PRs that need you" >/dev/null 2>&1 \
          || fail "Creating the public repo failed."
        ok "Created https://github.com/$REPO (public, one commit)"
        if [ "$WANT_SLACK" = "1" ]; then
          warn "Secrets don't move to the new repo. Run ./setup.sh again to reconnect Slack."
        fi
      fi
    fi
  fi
fi

if [ "$(gh repo view "$REPO" --json visibility --jq .visibility 2>/dev/null)" = "PUBLIC" ]; then
  VERSION="v$(python3 -c 'import scout; print(scout.__version__)')"
  if ! git ls-remote --tags origin "$VERSION" 2>/dev/null | grep -q .; then
    info "Tagging this version lets other people pin it, so later changes can't surprise them."
    if ask "Tag the current code as $VERSION?"; then
      git tag -a "$VERSION" -m "oss-scout $VERSION" && git push -q origin "$VERSION" && ok "Tagged $VERSION"
    fi
  fi
fi

# ───────────────────────────────────────────────────────────── 7
step 7 "Send the first digest"

info "The first run creates the board issue and posts a short 'watching from today' comment."
info "After that you only hear about changes, at 06:52 UTC on weekdays."
if ask "Run it now?"; then
  if run_and_watch "$REPO" digest.yml; then
    ok "Done"
    BOARD="$(gh issue list -R "$REPO" --label oss-scout --state open --json url --jq '.[0].url' 2>/dev/null || true)"
    if [ -n "$BOARD" ]; then
      info "Opening your board."
      open_url "$BOARD"
    fi
  fi
fi

# ───────────────────────────────────────────────────────────── 8
step 8 "Contribution log on your GitHub profile (optional)"

PROFILE="$HAVE/$HAVE"
info "Keeps a public, always-current list of your merged PRs, reviews, comments and issues"
info "in the README of $PROFILE, so it shows on https://github.com/$HAVE."
info "It's rebuilt from GitHub's own record every day; nothing to log by hand."
if [ "$(gh repo view "$REPO" --json visibility --jq .visibility 2>/dev/null)" != "PUBLIC" ]; then
  info "It runs the code from $REPO, so that repo needs to be public first (step 6). Skipping."
elif ask "Set it up?"; then
  if ! gh repo view "$PROFILE" >/dev/null 2>&1; then
    info "$PROFILE doesn't exist yet. It's a special public repo: its README shows on your profile."
    ask "Create it?" || fail "Skipped. Run ./setup.sh again when you want the log."
    gh repo create "$PROFILE" --public --add-readme \
      --description "My GitHub profile" >/dev/null || fail "Couldn't create $PROFILE."
    ok "Created https://github.com/$PROFILE"
  fi
  ORGS_DEFAULT="$(python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(" ".join(sorted({r["repo"].split("/")[0] for r in c["repos"]})))' "$CONFIG")"
  ORGS="$(prompt "GitHub organisations to log (space-separated)" "$ORGS_DEFAULT")"

  WORK="$(mktemp -d)"
  gh repo clone "$PROFILE" "$WORK/profile" -- -q || fail "Couldn't clone $PROFILE."
  mkdir -p "$WORK/profile/.github/workflows"
  sed "s#__SCOUT_REPO__#$REPO#" "$ROOT/profile-template/contrib-log.yml" \
    > "$WORK/profile/.github/workflows/contrib-log.yml"
  [ -f "$WORK/profile/annotations.yaml" ] || cp "$ROOT/profile-template/annotations.yaml" "$WORK/profile/annotations.yaml"
  LOGCFG="$WORK/profile/contrib-log.config.json"
  cfg_get() { python3 -c 'import json,os,sys; p=sys.argv[1]; c=json.load(open(p)) if os.path.exists(p) else {}; v=c
for k in sys.argv[2:]: v=(v or {}).get(k, "")
print(v or "")' "$LOGCFG" "$@"; }
  info "Optional: with your ORCID iD the log also lists your publications and peer reviews,"
  info "with citation counts. Only the public works and peer-review parts of ORCID are read."
  while :; do
    ORCID="$(prompt "ORCID iD (Enter to skip)" "$(cfg_get orcid)")"
    if [ -z "$ORCID" ] || [[ "$ORCID" =~ ^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$ ]]; then break; fi
    warn "An ORCID iD looks like 0000-0002-1825-0097."
  done
  LINKEDIN="$(prompt "LinkedIn profile URL for a link on your profile (Enter to skip)" "$(cfg_get links LinkedIn)")"
  python3 - "$LOGCFG" "$HAVE" "$ORGS" "https://github.com/$REPO" "$ORCID" "$LINKEDIN" <<'PY'
import json, os, sys
path, user, orgs, tool, orcid, linkedin = sys.argv[1], sys.argv[2], sys.argv[3].split(), sys.argv[4], sys.argv[5], sys.argv[6]
cfg = json.load(open(path)) if os.path.exists(path) else {}
cfg.update({"github_user": user, "orgs": orgs, "tool_url": tool})
cfg.setdefault("repos", [])
if orcid:
    cfg["orcid"] = orcid
else:
    cfg.pop("orcid", None)
links = cfg.get("links") or {}
if linkedin:
    links["LinkedIn"] = linkedin
else:
    links.pop("LinkedIn", None)
if links:
    cfg["links"] = links
else:
    cfg.pop("links", None)
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
PY
  README_FILE="$WORK/profile/README.md"
  if ! grep -q "oss-scout:intro" "$README_FILE" 2>/dev/null; then
    info "Visitors read the top of your profile first. A line about your focus does more than any count."
    info "Example: I work on Kubernetes observability, mostly OpenTelemetry collectors in real clusters."
    INTRO="$(prompt "One-line intro for the top of your profile (Enter to skip)" "")"
    if [ -n "$INTRO" ]; then
      python3 - "$README_FILE" "$INTRO" <<'PY'
import os, sys
path, intro = sys.argv[1], sys.argv[2]
text = open(path).read() if os.path.exists(path) else ""
lines = text.splitlines()
at = next((i + 1 for i, ln in enumerate(lines) if ln.strip()), 0)
block = ["", "<!-- oss-scout:intro -->", intro]
if at >= len(lines) or lines[at].strip():
    block.append("")
lines[at:at] = block
open(path, "w").write("\n".join(lines).rstrip("\n") + "\n")
PY
      ok "Intro added"
    fi
  fi
  git -C "$WORK/profile" config user.email "$(git config --get user.email)"
  git -C "$WORK/profile" config user.name "$(git config --get user.name)"
  git -C "$WORK/profile" add -A
  if git -C "$WORK/profile" diff --cached --quiet; then
    ok "The contribution log is already set up in $PROFILE"
  else
    git -C "$WORK/profile" commit -qm "Add an automatic contribution log" \
      && git -C "$WORK/profile" push -q || fail "Couldn't push to $PROFILE."
    ok "Added the daily workflow and config to $PROFILE"
  fi
  rm -rf "$WORK"

  info "The first run reads your whole history, so give it a few minutes."
  if ask "Build the log now?"; then
    if run_and_watch "$PROFILE" contrib-log.yml -f full=true; then
      ok "Done"
      info "Opening your profile."
      open_url "https://github.com/$HAVE"
      info "While you're there: 'Customize your pins' and pick the repos visitors should see first,"
      info "for example $REPO."
    fi
  fi
fi

# ───────────────────────────────────────────────────────────── 9
step 9 "Private portfolio (optional)"

info "A private repo that turns your log into a categorised portfolio every day: your own"
info "categories, words of recognition from maintainers, the people you've worked with,"
info "CV-ready lines and a month-by-month timeline. Only you can see it."
if ! gh api "repos/$PROFILE/contents/contrib-log.config.json" >/dev/null 2>&1; then
  info "It builds on the contribution log from step 8, which isn't set up yet. Skipping."
elif ask "Set it up?"; then
  PNAME="$(prompt "Name for the private repo" "portfolio")"
  PREPO="$HAVE/$PNAME"
  if gh repo view "$PREPO" >/dev/null 2>&1; then
    [ "$(gh repo view "$PREPO" --json visibility --jq .visibility 2>/dev/null)" = "PRIVATE" ] \
      || fail "$PREPO already exists and isn't private. Run again and pick another name."
    ok "Using your existing private repo $PREPO"
  else
    gh repo create "$PREPO" --private --add-readme \
      --description "Private contribution portfolio" >/dev/null || fail "Couldn't create $PREPO."
    ok "Created https://github.com/$PREPO (private)"
  fi
  WORK="$(mktemp -d)"
  gh repo clone "$PREPO" "$WORK/portfolio" -- -q || fail "Couldn't clone $PREPO."
  mkdir -p "$WORK/portfolio/.github/workflows"
  sed -e "s#__SCOUT_REPO__#$REPO#" -e "s#__PROFILE_REPO__#$PROFILE#" "$ROOT/portfolio-template/portfolio.yml" \
    > "$WORK/portfolio/.github/workflows/portfolio.yml"
  [ -f "$WORK/portfolio/notes.yaml" ] || cp "$ROOT/portfolio-template/notes.yaml" "$WORK/portfolio/notes.yaml"
  git -C "$WORK/portfolio" config user.email "$(git config --get user.email)"
  git -C "$WORK/portfolio" config user.name "$(git config --get user.name)"
  git -C "$WORK/portfolio" add -A
  if git -C "$WORK/portfolio" diff --cached --quiet; then
    ok "The portfolio is already set up in $PREPO"
  else
    git -C "$WORK/portfolio" commit -qm "Add the daily portfolio build" \
      && git -C "$WORK/portfolio" push -q || fail "Couldn't push to $PREPO."
    ok "Added the daily workflow and notes.yaml to $PREPO"
  fi
  rm -rf "$WORK"
  if ! gh api "repos/$PROFILE/contents/contributions.json" >/dev/null 2>&1; then
    info "Your log hasn't produced contributions.json yet; the portfolio builds daily once it has."
  elif ask "Build it now?"; then
    if run_and_watch "$PREPO" portfolio.yml; then
      ok "Done"
      info "Opening your portfolio."
      open_url "https://github.com/$PREPO/blob/main/portfolio.md"
    fi
  fi
  info "Make it yours: edit notes.yaml in $PREPO (categories, rules, private notes and entries)."
fi

printf '\n%sAll set.%s\n' "$G" "$N"
info "Board:  https://github.com/$REPO/issues?q=label%3Aoss-scout"
info "To get the comments on your phone, install the GitHub app and allow notifications."
info "Change what it watches: edit scout.config.json, then run ./setup.sh to push it"
info "Add notes or talks to your public log: edit annotations.yaml in $PROFILE"
info "Your private portfolio's categories and notes: notes.yaml in your portfolio repo"
