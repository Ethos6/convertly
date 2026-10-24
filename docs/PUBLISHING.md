# Publishing and locking down the repository

Everything below is scripted; it takes about two minutes.

## 1. Publish to GitHub

```bash
# once: install GitHub CLI (https://cli.github.com) and log in with an account that will own the repo
gh auth login

unzip convertly.zip && cd convertly
scripts/publish.sh                       # your personal account, public repo named "convertly"
scripts/publish.sh --owner atomesus      # or an organization
scripts/publish.sh --dry-run             # print every command without running it
```

The script fills in the owner in README/links, creates the repo, pushes `main`, sets squash-only merges and
branch auto-delete, turns on Dependabot alerts and fixes, secret scanning with push protection and private
vulnerability reporting, and creates three **rulesets**:

| Ruleset | Applies to | Blocks |
|---|---|---|
| `protect-default-branch` | `main` | force push, deletion, non-linear history, direct pushes (PR required), merging with red CI (`ci-ok`) |
| `protect-release-tags` | `v*` tags | deleting, moving or re-pointing a release tag |
| `no-force-push-on-release-branches` | `release/**` | force push, deletion |

No one is on the bypass list, including admins, so a mistaken `git push --force` from any machine is rejected
by GitHub. To change a rule later, edit the JSON in `.github/rulesets/` and re-run the script (it updates
rulesets with the same name).

## 2. Verify

```bash
scripts/verify-protection.sh OWNER/convertly          # reads the rules back
LIVE=1 scripts/verify-protection.sh OWNER/convertly   # also tries a real force push and branch delete (both must fail)
```

## 3. Protect the repository itself from deletion

Rulesets protect branches and tags; deleting the whole repository is an admin permission. To guard it:

- Keep admin rights to one or two people, with two-factor authentication on.
- In an organization: **Settings → Member privileges → Repository deletion and transfer**: off for members.
- Keep an off-site mirror: `git clone --mirror https://github.com/Ethos6/convertly.git` (a scheduled job is fine).
  Deleted repos can also be restored for 90 days from **Settings → Repositories → Deleted repositories**.

## 4. Release to PyPI (optional)

1. On https://pypi.org, add a *pending trusted publisher*: project `convertly-cli`, owner/repo, workflow
   `release.yml`, environment `pypi`.
2. In the GitHub repo: **Settings → Environments → New environment** `pypi` (add yourself as required reviewer),
   and **Settings → Variables** `PUBLISH_TO_PYPI = true`.
3. Bump `__version__` in `src/convertly/__init__.py`, update `CHANGELOG.md`, merge, then
   `git tag v0.1.0 && git push origin v0.1.0`. The workflow builds, attests provenance, creates the GitHub
   release, and publishes to PyPI. The tag can't be moved or deleted afterwards.
