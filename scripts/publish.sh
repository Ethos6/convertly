#!/usr/bin/env bash
# Publish convertly to GitHub and lock it down.
#
#   scripts/publish.sh [--owner NAME] [--repo convertly] [--private] [--dry-run]
#
# Needs: git, gh (logged in: `gh auth login` with repo + admin rights on the target).
# What it does (idempotent; safe to re-run):
#   1. fills in the OWNER placeholders, commits, creates the GitHub repo, pushes main
#   2. repo settings: squash-only merges, auto-delete merged branches, no wiki/projects
#   3. security: private vulnerability reporting, Dependabot alerts + fixes,
#      secret scanning + push protection
#   4. rulesets: no force-push / no deletion on main, release/** and v* tags
#      (+ PRs and green CI required for main)
set -euo pipefail

OWNER=""
REPO="convertly"
VISIBILITY="public"
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --owner) OWNER="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --private) VISIBILITY="private"; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

run() { if [ "$DRY" = 1 ]; then printf 'DRY: %q ' "$@"; echo; else "$@"; fi; }
api() { run gh api -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" "$@"; }

cd "$(dirname "$0")/.."
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required: https://cli.github.com" >&2; exit 1; }
if [ "$DRY" = 0 ]; then gh auth status >/dev/null || { echo "run: gh auth login" >&2; exit 1; }; fi
if [ -z "$OWNER" ]; then OWNER="$(gh api user --jq .login)"; fi
[[ "$OWNER" =~ ^[A-Za-z0-9-]+$ ]] || { echo "bad owner: $OWNER" >&2; exit 2; }
[[ "$REPO" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "bad repo: $REPO" >&2; exit 2; }
FULL="$OWNER/$REPO"
echo "==> publishing to https://github.com/$FULL ($VISIBILITY)"

# 1. placeholders + commit
if grep -rl --exclude-dir=.git --exclude-dir=.venv --exclude=publish.sh "OWNER" . >/dev/null 2>&1; then
  files=$(grep -rl --exclude-dir=.git --exclude-dir=.venv --exclude=publish.sh -e "github.com/OWNER/" -e "@OWNER" . || true)
  for f in $files; do
    run sed -i.bak -e "s#github.com/OWNER/convertly#github.com/$FULL#g" -e "s#github.com/OWNER/#github.com/$OWNER/#g" -e "s#@OWNER#@$OWNER#g" "$f"
    run rm -f "$f.bak"
  done
fi
[ -d .git ] || run git init -b main
run git add -A
if [ "$DRY" = 1 ] || ! git diff --cached --quiet; then
  run git commit -m "Set repository owner to $OWNER"
fi

# 2. create + push
if [ "$DRY" = 0 ] && gh repo view "$FULL" >/dev/null 2>&1; then
  echo "    repo exists, pushing"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$FULL.git"
  git push -u origin main
else
  run gh repo create "$FULL" "--$VISIBILITY" --source=. --remote=origin --push \
    --description "Private, local file conversion: CLI, Python API and a drag-and-drop web UI. Nothing is uploaded."
fi

# 3. settings
api -X PATCH "repos/$FULL" \
  -F has_wiki=false -F has_projects=false -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F allow_squash_merge=true -F delete_branch_on_merge=true -F allow_auto_merge=true \
  -F web_commit_signoff_required=false >/dev/null
api -X PUT "repos/$FULL/topics" -f "names[]=file-converter" -f "names[]=cli" -f "names[]=privacy" \
  -f "names[]=offline" -f "names[]=pdf" -f "names[]=ffmpeg" -f "names[]=python" >/dev/null
api -X PUT "repos/$FULL/private-vulnerability-reporting" >/dev/null || echo "    (private vulnerability reporting: enable in Settings → Security)"
api -X PUT "repos/$FULL/vulnerability-alerts" >/dev/null || true
api -X PUT "repos/$FULL/automated-security-fixes" >/dev/null || true
if [ "$VISIBILITY" = public ]; then
  api -X PATCH "repos/$FULL" --input - >/dev/null <<'JSON' || echo "    (secret scanning: enable in Settings → Security)"
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}
JSON
fi

# 4. rulesets (replace same-named ones so re-runs stay in sync)
for f in .github/rulesets/*.json; do
  name=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" "$f")
  id=""
  if [ "$DRY" = 0 ]; then
    id=$(gh api "repos/$FULL/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null || true)
  fi
  if [ -n "$id" ]; then
    api -X PUT "repos/$FULL/rulesets/$id" --input "$f" >/dev/null && echo "    updated ruleset $name"
  else
    api -X POST "repos/$FULL/rulesets" --input "$f" >/dev/null && echo "    created ruleset $name"
  fi
done

echo "==> done. Verify with: scripts/verify-protection.sh $FULL"
echo "    Note: rulesets can't stop the repo itself being deleted by an admin. Keep admin to yourself,"
echo "    turn on 2FA, and in an org set 'Repository deletion' to owners only (Org → Settings → Member privileges)."
