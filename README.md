# GitHub Team Stats

Pulls PR, commit, and collaboration stats per team member from a GitHub org.

## Quick Install (macOS)

Open Terminal and run:

```bash
curl -O https://raw.githubusercontent.com/KvaddeML919/GitHub-basic-stats-karteek/main/install.sh && bash install.sh
```

The installer will:

1. Clone the repo to `~/github-stats`
2. Install Python dependencies
3. Ask for your GitHub organization name (saved for future runs)
4. Prompt you to enter team member GitHub usernames (saved for future runs)
5. Create a **"GitHub Stats"** shortcut on your Desktop

### Prerequisites

- macOS with Python 3 (pre-installed on modern Macs)
- A GitHub Classic Personal Access Token (see [Token Setup](#github-token-setup) below)

## Usage

Double-click **"GitHub Stats"** on your Desktop. It will:

1. Ask you to paste your GitHub token (or pick it up from `GITHUB_TOKEN` env var if set)
2. Validate the token -- clear error messages if scopes are missing or SSO isn't configured
3. Ask how many days to look back (default: 90)
4. Run the stats and display results
5. Export a timestamped CSV to `~/github-stats/`

## Metrics

| Category | Metric | Description |
|---|---|---|
| **Activity** | Total PRs | PRs opened in the lookback period |
| | PRs per working day | PRs / weekdays (Mon-Fri) |
| | Merged PRs & Merge Rate | Count and percentage of PRs that were merged |
| | Total Commits | Commits in the lookback period |
| | Commits per working day | Commits / weekdays |
| | Avg Coding Days/Week | Average weekday days with at least 1 commit, per active week (zero-commit weeks excluded) |
| **Collaboration** | Reviews Given | PRs formally reviewed by the user |
| | PRs Commented On | Other people's PRs where the user left comments |
| **Quality** | Avg Time-to-Merge | Average hours from PR creation to merge |
| | Active Repos | Distinct repositories the user committed to |
| | Avg Lines per Commit | Average additions and deletions per commit (sampled from 5 recent commits) |

### Output

- **Per-user progress** printed as it runs
- **Two summary tables** (sorted by most commits):
  - **Activity** -- PRs, PRs/wd, Merged%, Commits, Cmts/wd, CdDays/wk
  - **Collaboration & Quality** -- Reviews, Commented, Merge(h), Repos, +Lines/c, -Lines/c
- **A timestamped CSV** (e.g. `github_stats_20260318_120000.csv`) with all metrics

## GitHub Token Setup

Create a **Classic** Personal Access Token at https://github.com/settings/tokens

### Required scopes

- **`repo`** (top-level checkbox -- "Full control of private repositories"). Do NOT just select sub-scopes like `repo:status` or `public_repo` -- those won't give access to private repos.
- **`read:org`**

### SSO authorization (required for enterprise orgs)

If your GitHub org uses SAML SSO (most enterprise orgs do):

1. Go to https://github.com/settings/tokens
2. Find your token in the list
3. Click **"Configure SSO"** next to it
4. **Authorize** it for your organization
5. Complete the SSO login if prompted

Without this step, all stats will return as zero even if the token scopes are correct.

> **Note:** After updating token scopes or enabling SSO, GitHub may take 1-2 minutes to propagate the changes. If you still see zeros right after updating, wait a minute and try again.

## Configuration

### Organization

Edit `~/github-stats/org.txt` to change the GitHub org. This is set during install and is gitignored.

### Team members

Edit `~/github-stats/team.txt` (one GitHub username per line). This is set during install and is gitignored.

### Other settings

Edit the top of `github_stats.py` to change:
- `DEFAULT_LOOKBACK_DAYS` -- default when you press Enter at the prompt (default: 90)

## Manual Setup (alternative)

If you prefer not to use the installer:

```bash
git clone https://github.com/KvaddeML919/GitHub-basic-stats-karteek.git ~/github-stats
cd ~/github-stats
pip3 install -r requirements.txt
```

Create `org.txt` with your GitHub org name and `team.txt` with one GitHub username per line, then run:

```bash
python3 github_stats.py
```

You can also pass the lookback period as an argument:

```bash
python3 github_stats.py 30
```

## Updating

To get the latest version, re-run the install command or:

```bash
cd ~/github-stats && git pull
```

Your `org.txt` and `team.txt` will be preserved since they're gitignored.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Token is invalid or expired" | Token was revoked or mistyped | Create a new token at github.com/settings/tokens |
| "Missing required scope(s): repo" | Token scopes incomplete | Edit the token and check the top-level `repo` checkbox |
| "Cannot access the org" | Token not SSO-authorized | Configure SSO (see above) |
| All stats zero | Token scopes or SSO issue | Check the error messages from token validation |
| Some users zero | Username doesn't match their GitHub account | Verify at `github.com/<username>` |
| `pip: command not found` | macOS uses `pip3` | Use `pip3 install -r requirements.txt` |
| `python: command not found` | macOS uses `python3` | Use `python3 github_stats.py` |
| Rate limit errors | Too many runs in a short period | Wait a few minutes and retry |
