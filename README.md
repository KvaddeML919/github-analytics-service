# GitHub Team Stats

A simple command-line tool that pulls **PR, commit, and collaboration metrics** for your engineering team from GitHub. Run it, pick a team, and get a summary table + Excel report in minutes.

Works on **macOS** and **Windows**.

**What you get per engineer:**

| Activity | Collaboration | Quality |
|---|---|---|
| PRs opened, merge rate | Reviews given | Avg merge time |
| Commits, coding days/week | PRs commented on | Active repos |
| Weekend commits | | |

---

## Prerequisites

| Requirement | macOS | Windows |
|---|---|---|
| **Python 3.9+** | Usually pre-installed or from [python.org](https://www.python.org/downloads/) | Install from [python.org](https://www.python.org/downloads/) — check **"Add python.exe to PATH"** |
| **Git** | `xcode-select --install` or [git-scm.com](https://git-scm.com) | [Git for Windows](https://git-scm.com/download/win) |
| **GitHub token** | Classic PAT with `repo` + `read:org` (see Step 1) | Same |

Install folder (both platforms): **`~/github-analytics-service`** (Mac) or **`%USERPROFILE%\github-analytics-service`** (Windows).

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

#### macOS

Open **Terminal** (Spotlight → type "Terminal") and paste:

```bash
curl -O https://raw.githubusercontent.com/KvaddeML919/github-analytics-service/main/install.sh && bash install.sh
```

#### Windows

Open **PowerShell** (Start → type "PowerShell") and paste:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
irm https://raw.githubusercontent.com/KvaddeML919/github-analytics-service/main/install.ps1 | iex
```

If your company blocks `irm | iex`, download and run instead:

```powershell
curl -o install.ps1 https://raw.githubusercontent.com/KvaddeML919/github-analytics-service/main/install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer will ask you a few things:

| Prompt | What to enter |
|---|---|
| **Organization name** | Your GitHub org (e.g. `my-company`) |
| **GitHub API base URL** | Press Enter for GitHub.com, or enter your Enterprise API URL, such as `https://github.example.com/api/v3` |
| **Team name** | A label for each team (e.g. `Backend`, `Payments`) |
| **Usernames** | GitHub usernames of team members, one at a time |

After the first organization and teams are set up, the installer asks whether you want to add another organization profile. For each additional profile, enter a local profile name, organization name, API base URL, and teams. The token is not requested during installation because it is never saved; the desktop shortcut requests the token securely when you run a selected profile.

Press Enter on an empty line to move to the next team or finish.

When done, you'll see:

```
=========================================
  Installation complete!
=========================================

  To run:  Double-click the shortcut on your Desktop
```

| Platform | Desktop shortcut |
|---|---|
| macOS | **GitHub Stats** |
| Windows | **GitHub Stats.bat** |

### Step 3 — Run

Double-click the desktop shortcut (**GitHub Stats** on Mac, **GitHub Stats.bat** on Windows).

The tool will prompt you for:

1. **Which organization profile** — when multiple profiles were installed
2. **Your GitHub token** — paste the token for the selected server and organization
3. **Which team** — run for all teams or pick one
4. **Lookback period** — how many days back (default: 90)

It then fetches data from GitHub and prints results to the terminal. When finished, it also saves an Excel file (`github_stats_YYYYMMDD_HHMMSS.xlsx`) in your install folder.

For a GitHub Enterprise Server installation, enter the server's API base URL when the installer asks. For example, if the organization page is `https://github.example.com/tc`, enter `https://github.example.com/api/v3` and enter `tc` as the organization name. Do not enter the organization web page URL as the API base.

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
Username              Reviews  Commented  Merge Time  Repos
──────────────────────────────────────────────────────────────
alice                      19         14       24.3h      6
bob                        44         23       12.1h      3
carol                      13          4       18.7h      5
```

The Excel file contains the same data with styled headers, alternating row colors, and a team average row -- ready to share.

### Multiple Organizations

For more than one organization, create one profile folder per organization. Profile names are local labels you choose; they do not need to match the organization name. Each profile can also contain `api.txt` when the organization is hosted on GitHub Enterprise:

```bash
mkdir -p ~/github-analytics-service/profiles/company-a ~/github-analytics-service/profiles/company-b
printf 'company-a\n' > ~/github-analytics-service/profiles/company-a/org.txt
printf 'company-b\n' > ~/github-analytics-service/profiles/company-b/org.txt
```

GitHub.com profiles use `https://api.github.com` automatically. For a GitHub Enterprise Server organization such as `https://github.example.com/tc`, create `profiles/enterprise-tc/api.txt` with:

```text
https://github.example.com/api/v3
```

The Enterprise profile's `org.txt` should contain `tc`, not the full URL.

Create a separate `team.txt` in each profile using the team format below. Tokens are never saved by the tool. The token variable uses the profile name: replace non-alphanumeric characters with `_`, uppercase it, and add `GITHUB_TOKEN_`. For example, profile `enterprise-tc` uses `GITHUB_TOKEN_ENTERPRISE_TC`:

```bash
export GITHUB_TOKEN_COMPANY_A='token-for-company-a'
export GITHUB_TOKEN_ENTERPRISE_TC='token-for-enterprise-tc'
```

Run with a profile explicitly, or omit `--profile` to choose interactively when multiple profiles exist:

```bash
cd ~/github-analytics-service
python3 github_stats.py --profile company-a 90
python3 github_stats.py --profile company-b 90
```

If the profile token environment variable is not set, the tool prompts for that profile's token. The original `org.txt`, `team.txt`, and `GITHUB_TOKEN` setup remains supported as the `default` profile.

---

## Day-to-Day Usage

| Task | macOS | Windows |
|---|---|---|
| **Run the tool** | Double-click **GitHub Stats** | Double-click **GitHub Stats.bat** |
| **Run from terminal** | `cd ~/github-analytics-service && python3 github_stats.py` | `cd %USERPROFILE%\github-analytics-service` then `python github_stats.py` |
| **Custom lookback** | `python3 github_stats.py 30` | `python github_stats.py 30` |
| **Edit teams** | Edit `~/github-analytics-service/team.txt` or `profiles/<name>/team.txt` | Edit `%USERPROFILE%\github-analytics-service\team.txt` or `profiles\<name>\team.txt` |
| **Change org** | Edit `~/github-analytics-service/org.txt` or `profiles/<name>/org.txt` | Edit `%USERPROFILE%\github-analytics-service\org.txt` or `profiles\<name>\org.txt` |
| **Change API host** | Edit `profiles/<name>/api.txt` for GitHub Enterprise | Edit `profiles\<name>\api.txt` for GitHub Enterprise |
| **Update the tool** | `cd ~/github-analytics-service && git pull` | `cd %USERPROFILE%\github-analytics-service` then `git pull` |

Your `org.txt`, `team.txt`, `api.txt`, and `profiles/` directory are gitignored, so `git pull` won't overwrite them.

---

## Team File Format

Edit `team.txt` in your install folder to add or remove members:

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
| **Weekend Commits** | Unique non-merge commits on Sat/Sun |

### Collaboration

| Metric | What it measures |
|---|---|
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
| **Weekend Commits** | `count(non-merge commits where author date falls on Sat/Sun within window)` |
| **Avg Merge Time (hrs)** | `mean(merged_at - created_at) for each merged PR` |
| **Active Repos** | `count(distinct repos with commits in window)` |
| **Reviews Given** | `count(PRs where user submitted a review)` |
| **PRs Commented On** | `count(others' PRs where user left a comment)` |

---

## If installation fails

The installer checks **Git**, **Python 3**, and **pip** first, then installs packages and verifies `requests` and `openpyxl` import correctly. If anything fails, read the error in the terminal — it includes manual recovery steps.

### macOS — manual recovery

```bash
cd ~/github-analytics-service
python3 -m pip install -r requirements.txt
python3 -c "import requests, openpyxl; print('OK')"
python3 github_stats.py
```

| Installer message | What to do |
|---|---|
| **Git is not installed** | Run `xcode-select --install` or install from [git-scm.com](https://git-scm.com) |
| **Python 3 is not installed** | Install from [python.org](https://www.python.org/downloads/) |
| **pip is not available** | Run `python3 -m ensurepip --upgrade` then retry |
| **Failed to install dependencies** | Run the commands above; copy any pip error (SSL, permission, network) |
| **Import check failed** | Re-run `python3 -m pip install -r requirements.txt` |

### Windows — manual recovery

```powershell
cd $env:USERPROFILE\github-analytics-service
python -m pip install -r requirements.txt
python -c "import requests, openpyxl; print('OK')"
python github_stats.py
```

If `python` is not found, try `py -3` instead of `python` in each command.

| Installer message | What to do |
|---|---|
| **Git is not installed** | Install [Git for Windows](https://git-scm.com/download/win), restart PowerShell |
| **Python 3 not found** | Install from [python.org](https://www.python.org/downloads/) with **Add to PATH** checked |
| **Failed to install dependencies** | Run the commands above in PowerShell as your normal user |
| **Import check failed** | Re-run `python -m pip install -r requirements.txt` |

### Desktop shortcut says "Missing dependencies"

Dependencies were not installed or Python changed after install. Run the manual recovery commands for your platform, then try the shortcut again.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "Token is invalid or expired" | Create a new token at [github.com/settings/tokens](https://github.com/settings/tokens) |
| "Missing required scope(s): repo" | Edit your token and check the top-level `repo` checkbox |
| "Cannot access the org" | Authorize SSO for your token (see [Step 1](#step-1--create-a-github-token)) |
| "Organization not found" | Check `org.txt` — org name must match GitHub exactly |
| All stats are zero | Token scopes or SSO issue -- check the error messages |
| Some users show zero | Verify their GitHub username at `github.com/<username>` |
| Installer or pip errors | See [If installation fails](#if-installation-fails) |
| `pip: command not found` | Use `python -m pip install -r requirements.txt` (Windows) or `python3 -m pip` (Mac) |
| `python: command not found` | Install Python and add to PATH; on Windows try `py github_stats.py` |
| `ModuleNotFoundError: requests` or `openpyxl` | `cd` to install folder and run `pip install -r requirements.txt` |
| PowerShell script blocked | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` or use `-ExecutionPolicy Bypass` |
| Git not found (Windows) | Install [Git for Windows](https://git-scm.com/download/win) and restart PowerShell |
| Rate limit errors | Wait a few minutes and retry |
| Slow run | Normal for many PRs -- use a shorter lookback or pick a specific team |
