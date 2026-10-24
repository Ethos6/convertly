#!/usr/bin/env bash
# Check that force pushes and deletions are blocked:  scripts/verify-protection.sh OWNER/REPO
set -euo pipefail
FULL="${1:?usage: verify-protection.sh OWNER/REPO}"
fail=0
check() { local label="$1"; shift; if "$@"; then echo "  ok   $label"; else echo "  FAIL $label"; fail=1; fi; }
# shellcheck disable=SC2329  # called through check()
has() { [[ " $1 " == *" $2 "* ]]; }

rules=$(gh api "repos/$FULL/rules/branches/main" --jq '[.[].type] | join(" ")')
check "main: deletion blocked"       has "$rules" deletion
check "main: force push blocked"     has "$rules" non_fast_forward
check "main: pull request required"  has "$rules" pull_request
check "main: CI must pass"           has "$rules" required_status_checks
tags=$(gh api "repos/$FULL/rulesets" --jq '[.[] | select(.target=="tag") | .name] | join(" ")')
check "tags: release tags protected" has "$tags" protect-release-tags
enf=$(gh api "repos/$FULL/rulesets" --jq '[.[] | .enforcement] | unique | join(" ")')
check "all rulesets enforced"        test "$enf" = active

# Live test: a force push to main must be rejected.
if [ "${LIVE:-0}" = 1 ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  git clone -q -b main "${REMOTE_URL:-https://github.com/$FULL.git}" "$tmp/r" && cd "$tmp/r"
  git commit -q --amend -m "force-push probe" --allow-empty
  if git push -q --force origin HEAD:main 2>/dev/null; then echo "  FAIL force push was accepted!"; fail=1
  else echo "  ok   live force push rejected"; fi
  if git push -q origin --delete main 2>/dev/null; then echo "  FAIL branch deletion was accepted!"; fail=1
  else echo "  ok   live branch deletion rejected"; fi
fi
exit $fail
