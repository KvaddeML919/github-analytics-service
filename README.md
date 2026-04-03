# GitHub Team Stats

Generates per-engineer PR, commit, and collaboration metrics for a GitHub organization. Outputs to console and a timestamped Excel file.

---

## 1. Prerequisites

- **macOS** with Python 3 (pre-installed on modern Macs)
- A **GitHub Classic Personal Access Token** — see [Token Setup](#3-github-token-setup)

## 2. Installation

### Quick install (recommended)

```bash
curl -O https://raw.githubusercontent.com/KvaddeML919/GitHub-basic-stats-karteek/main/install.sh && bash install.sh
```

The installer clones the repo to `~/github-stats`, installs dependencies, asks for your org name and team usernames, and creates a **"GitHub Stats"** Desktop shortcut.

### Manual install

```bash
git clone https://github.com/KvaddeML919/GitHub-basic-stats-karteek.git ~/github-stats
cd ~/github-stats
pip3 install -r requirements.txt
```

Then create two files in `~/github-stats/`:

- **`org.txt`** — your GitHub org name (one line, e.g. `my-company`)
- **`team.txt`** — team members grouped by team:

```
[Payments]
alice
bob

[Platform]
carol
dave
```

Members without a `[TeamName]` header go into "Ungrouped".

## 3. GitHub Token Setup

Create a **Classic** token at https://github.com/settings/tokens.

**Required scopes:**

| Scope | Why |
|---|---|
| `repo` (top-level checkbox) | Access private repos. Don't just select sub-scopes like `repo:status`. |
| `read:org` | Read org membership |

**If your org uses SAML SSO** (most enterprise orgs):

1. Go to https://github.com/settings/tokens
2. Click **Configure SSO** next to your token
3. **Authorize** it for your organization

Without SSO authorization, all stats will return zero even with correct scopes.

> Token/SSO changes may take 1–2 minutes to propagate.

## 4. Running the Tool

**Option A:** Double-click the **"GitHub Stats"** shortcut on your Desktop.

**Option B:** Run from terminal:

```bash
cd ~/github-stats
python3 github_stats.py        # default 90-day lookback
python3 github_stats.py 30     # custom lookback (days)
```

The tool will:

1. Prompt for your token (or read from `GITHUB_TOKEN` env var)
2. Validate token scopes and SSO access
3. Ask to run for **all teams** or a **specific team**
4. Ask lookback period (default: 90 days)
5. Print per-user progress, then summary tables
6. Export a timestamped `.xlsx` file to `~/github-stats/`

## 5. Understanding the Metrics

All times are in **MYT (UTC+8)**. Commit metrics use **author date** (when code was written, not when it was rebased/pushed) and include PR branch commits — not just default-branch commits — so results are accurate even with squash-merge and rebase workflows.

The tool discovers commits from three sources:
- **Default-branch commits** via GitHub's Search API
- **PR branch commits** for all PRs created within the lookback period (merged, open, draft, and closed)
- **Older PR branch commits** for PRs created before the lookback period that are still open or were merged during it

### Activity

| Metric | What it tells you |
|---|---|
| **Total PRs** | PRs opened in the lookback period |
| **PRs / Working Day** | PRs per weekday (Mon–Fri) — measures PR throughput |
| **Merged PRs / Merge Rate %** | How many PRs were merged and at what rate |
| **Total Commits** | Unique commits authored within the lookback period (default branch + PR branches), excluding merge commits |
| **Commits / Day** | Average commits per coding day — measures intensity on active days |
| **Coding Days / Week** | Average days per week with at least one non-merge commit. Only active weeks count; partial weeks are normalized. Aligns with [Flow's definition](https://appfire.atlassian.net/wiki/spaces/FD/pages/1802502326/Coding+days) |
| **Weekend Commits** | Commits authored on Sat/Sun within the lookback period |

### Collaboration

| Metric | What it tells you |
|---|---|
| **Reaction Time (hrs)** | Average hours from PR creation until the first review or comment from a teammate — measures how quickly the team responds to new PRs |
| **Time to 1st Comment (hrs)** | Average hours from PR creation until the first comment from a teammate — similar to reaction time but comment-only |
| **Reviews Given** | PRs where the user submitted a formal review (Approve / Request Changes / Comment) |
| **PRs Commented On** | Other people's PRs where the user left comments (excludes own PRs) |

### Quality

| Metric | What it tells you |
|---|---|
| **Avg Merge Time (hrs)** | Average hours from PR creation to merge — lower is faster turnaround |
| **Active Repos** | Distinct repos the user committed to — measures breadth of contribution |

### How to interpret

- **High Coding Days + low Commits/Day** → steady, spread-out work
- **Low Coding Days + high Commits/Day** → bursty, concentrated coding sessions
- **High PRs but low Merge Rate** → possible bottleneck in reviews or PR quality
- **High Reaction Time** → PRs sit waiting for feedback — review process may need attention
- **High Reviews Given** → active code reviewer, contributing to team quality

## 6. Output Format

- **Console:** two summary tables (Activity, Collaboration & Quality) sorted by total commits, with a **Team Average** row at the bottom
- **Excel:** timestamped file (e.g. `github_stats_20260319_120000.xlsx`)
  - **All teams** → "All" sheet + one sheet per team
  - **Specific team** → single sheet
  - Each sheet includes a styled **Team Average** row below the individual rows (when 2+ members)

## 7. Configuration

| File | Purpose | Set during install? | Gitignored? |
|---|---|---|---|
| `org.txt` | GitHub org name | Yes | Yes |
| `team.txt` | Team members and groupings | Yes | Yes |
| `github_stats.py` (top) | `DEFAULT_LOOKBACK_DAYS` (default: 90) | No | No |

## 8. Updating

```bash
cd ~/github-stats && git pull
```

`org.txt` and `team.txt` are preserved (gitignored).

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| "Token is invalid or expired" | Create a new token at github.com/settings/tokens |
| "Missing required scope(s): repo" | Edit token → check the top-level `repo` checkbox |
| "Cannot access the org" | Configure SSO for your token (see [Token Setup](#3-github-token-setup)) |
| All stats zero | Token scopes or SSO issue — check the error messages |
| Some users zero | Verify the GitHub username at `github.com/<username>` |
| `pip: command not found` | Use `pip3 install -r requirements.txt` |
| `python: command not found` | Use `python3 github_stats.py` |
| Rate limit errors | Wait a few minutes and retry |
| Slow run | Normal for users with many PRs — use a shorter lookback or run a specific team |

## 10. Formulas

| Metric | Formula |
|---|---|
| **PRs / Working Day** | `total_prs / weekdays_in_period` |
| **Merge Rate %** | `merged_prs / total_prs × 100` |
| **Total Commits** | `count(unique commits by author date in window, excluding merge commits)` |
| **Commits / Day** | `total_commits / coding_days` |
| **Coding Days / Week** | `(coding_days / days_in_active_weeks) × min(7, days_in_active_weeks)` — [Flow formula](https://appfire.atlassian.net/wiki/spaces/FD/pages/1802502326/Coding+days) |
| **Weekend Commits** | `count(commits where author date falls on Sat/Sun within window)` |
| **Avg Merge Time (hrs)** | `mean(merged_at − created_at) for each merged PR` |
| **Active Repos** | `count(distinct repos with commits in window)` |
| **Reaction Time (hrs)** | `mean(first_review_or_comment_at − created_at) for each PR` |
| **Time to 1st Comment (hrs)** | `mean(first_comment_at − created_at) for each PR` |
| **Reviews Given** | `count(PRs where user submitted a review)` |
| **PRs Commented On** | `count(others' PRs where user left a comment)` |
