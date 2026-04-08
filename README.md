# GitHub Team Stats

A simple command-line tool that pulls **PR, commit, and collaboration metrics** for your engineering team from GitHub. Run it, pick a team, and get a summary table + Excel report in minutes.

**What you get per engineer:**

| Activity | Collaboration | Quality |
|---|---|---|
| PRs opened, merge rate | Reviews given | Avg merge time |
| Commits, coding days/week | PRs commented on | Active repos |
| Weekend commits | Reaction time to PRs | Time to first comment |

---

## Quick Start (5 minutes)

### Step 1 — Create a GitHub Token

You need a **Classic Personal Access Token** to access your org's data.

1. Go to https://github.com/settings/tokens → **Generate new token (classic)**
2. Check these scopes:
   - **`repo`** (the top-level checkbox — not just sub-scopes)
   - **`read:org`**
3. Click **Generate token** and copy it

**If your org uses SAML SSO** (most enterprise orgs):

4. On the same tokens page, click **Configure SSO** next to your new token
5. Click **Authorize** for your organization

> Without SSO authorization the tool will run but return zero results.

### Step 2 — Install

Open **Terminal** (Spotlight → type "Terminal") and paste:

```bash
curl -O https://raw.githubusercontent.com/KvaddeML919/github-analytics-service/main/install.sh && bash install.sh
```

The installer will ask you a few things:

| Prompt | What to enter |
|---|---|
| **Organization name** | Your GitHub org (e.g. `my-company`) |
| **Team name** | A label for each team (e.g. `Backend`, `Payments`) |
| **Usernames** | GitHub usernames of team members, one at a time |

Press Enter on an empty line to move to the next team or finish.

When done, you'll see:

```
=========================================
  Installation complete!
=========================================

  To run:  Double-click 'GitHub Stats' on your Desktop
```

### Step 3 — Run

Double-click the **"GitHub Stats"** shortcut on your Desktop.

The tool will prompt you for:

1. **Your GitHub token** — paste the token from Step 1
2. **Which team** — run for all teams or pick one
3. **Lookback period** — how many days back (default: 90)

It then fetches data from GitHub and prints results to the terminal. When finished, it also saves an Excel file (`github_stats_YYYYMMDD_HHMMSS.xlsx`) in `~/github-stats/`.

---

## Sample Output

```
ACTIVITY
Username                PRs  PRs/Day  Merged%  Commits  Commits/Day  Coding Days  Wknd Commits
──────────────────────────────────────────────────────────────────────────────────────────────
alice                    24     1.04    79.2%      122          6.8          4.1             0
bob                      25     1.09    92.0%      100          5.0          4.5             0
carol                    14     0.61    92.9%       69          5.3          2.9             0
──────────────────────────────────────────────────────────────────────────────────────────────
TEAM AVERAGE                     0.9    88.0%                   5.7          3.8             0

COLLABORATION & QUALITY
Username              Reviews  Commented  Reaction  1st Cmt  Merge Time  Repos
──────────────────────────────────────────────────────────────────────────────
alice                      19         14      1.2h     1.5h       24.3h      6
bob                        44         23      0.5h     0.8h       12.1h      3
carol                      13          4      2.1h     3.0h       18.7h      5
```

The Excel file contains the same data with styled headers, alternating row colors, and a team average row -- ready to share.

---

## Day-to-Day Usage

| Task | How |
|---|---|
| **Run the tool** | Double-click **"GitHub Stats"** on your Desktop |
| **Run from terminal** | `cd ~/github-stats && python3 github_stats.py` |
| **Custom lookback** | `python3 github_stats.py 30` (30 days instead of 90) |
| **Edit teams** | Edit `~/github-stats/team.txt` in any text editor |
| **Change org** | Edit `~/github-stats/org.txt` |
| **Update the tool** | `cd ~/github-stats && git pull` |

Your `org.txt` and `team.txt` are gitignored, so `git pull` won't overwrite them.

---

## Team File Format

Edit `~/github-stats/team.txt` to add or remove members:

```
[Payments]
alice
bob

[Platform]
carol
dave
```

Each `[TeamName]` header starts a group. The tool lets you run reports per team or across all teams. Members without a header go into "Ungrouped".

---

## Metrics Reference

All times are in **MYT (UTC+8)**. The lookback window ends at **yesterday** (today is never included, matching Flow's convention). Commit metrics use **author date** (when code was written, not when it was rebased/pushed) and exclude merge commits.

### Activity

| Metric | What it measures |
|---|---|
| **Total PRs** | PRs opened in the lookback period |
| **PRs / Working Day** | PRs per weekday (Mon-Fri) |
| **Merge Rate %** | Percentage of PRs that were merged |
| **Total Commits** | Unique non-merge commits authored in the period (default branch + PR branches) |
| **Commits / Day** | Commits per coding day -- intensity on active days |
| **Coding Days / Week** | Days per week with at least one commit (only active weeks count) |
| **Weekend Commits** | Commits on Sat/Sun |

### Collaboration

| Metric | What it measures |
|---|---|
| **Reaction Time (hrs)** | Hours until first review or comment from a teammate |
| **Time to 1st Comment (hrs)** | Hours until first comment from a teammate |
| **Reviews Given** | PRs where the user submitted a review |
| **PRs Commented On** | Others' PRs where the user left comments |

### Quality

| Metric | What it measures |
|---|---|
| **Avg Merge Time (hrs)** | Hours from PR creation to merge |
| **Active Repos** | Distinct repos the user committed to |

### Reading the Numbers

- **High Coding Days + low Commits/Day** -- steady, spread-out work
- **Low Coding Days + high Commits/Day** -- bursty, concentrated sessions
- **High PRs but low Merge Rate** -- possible review bottleneck
- **High Reaction Time** -- PRs waiting for feedback
- **High Reviews Given** -- active code reviewer

---

## Formulas

| Metric | Formula |
|---|---|
| **PRs / Working Day** | `total_prs / weekdays_in_period` |
| **Merge Rate %** | `merged_prs / total_prs * 100` |
| **Total Commits** | `count(unique commits by author date in window, excluding merge commits)` |
| **Commits / Day** | `total_commits / coding_days` |
| **Coding Days / Week** | `(coding_days / days_in_active_weeks) * min(7, days_in_active_weeks)` |
| **Weekend Commits** | `count(commits where author date falls on Sat/Sun within window)` |
| **Avg Merge Time (hrs)** | `mean(merged_at - created_at) for each merged PR` |
| **Active Repos** | `count(distinct repos with commits in window)` |
| **Reaction Time (hrs)** | `mean(first_review_or_comment_at - created_at) for each PR` |
| **Time to 1st Comment (hrs)** | `mean(first_comment_at - created_at) for each PR` |
| **Reviews Given** | `count(PRs where user submitted a review)` |
| **PRs Commented On** | `count(others' PRs where user left a comment)` |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "Token is invalid or expired" | Create a new token at [github.com/settings/tokens](https://github.com/settings/tokens) |
| "Missing required scope(s): repo" | Edit your token and check the top-level `repo` checkbox |
| "Cannot access the org" | Authorize SSO for your token (see [Step 1](#step-1--create-a-github-token)) |
| All stats are zero | Token scopes or SSO issue -- check the error messages |
| Some users show zero | Verify their GitHub username at `github.com/<username>` |
| `pip: command not found` | Use `pip3 install -r requirements.txt` |
| `python: command not found` | Use `python3 github_stats.py` |
| Rate limit errors | Wait a few minutes and retry |
| Slow run | Normal for many PRs -- use a shorter lookback or pick a specific team |
