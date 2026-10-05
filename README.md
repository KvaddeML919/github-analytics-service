# GitHub Team Stats

A command-line tool that collects GitHub pull request, commit, review, and repository metrics for configured team members. It prints a summary and exports an Excel workbook.

Works on **macOS** and **Windows**.

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

### Multiple Organizations

Each organization profile lives in `profiles/<profile-name>/` and contains `org.txt` and `team.txt`. Profile names are local labels. GitHub.com uses its API by default; for GitHub Enterprise, add `api.txt` with the HTTPS API base URL (for example, `https://github.example.com/api/v3`). Put only the organization name, not its web URL, in `org.txt`.

```bash
mkdir -p ~/github-analytics-service/profiles/company-a ~/github-analytics-service/profiles/company-b
printf 'company-a\n' > ~/github-analytics-service/profiles/company-a/org.txt
printf 'company-b\n' > ~/github-analytics-service/profiles/company-b/org.txt
```

Tokens are not saved. Set `GITHUB_TOKEN_<PROFILE>` (uppercase the profile name and replace punctuation with `_`) or enter it when prompted. For example:

```bash
export GITHUB_TOKEN_COMPANY_A='token-for-company-a'
export GITHUB_TOKEN_ENTERPRISE_TC='token-for-enterprise-tc'
```

Run a profile explicitly, or omit `--profile` to choose interactively:

```bash
cd ~/github-analytics-service
python3 github_stats.py --profile company-a 90
python3 github_stats.py --profile company-b 90
```

The original root-level `org.txt`, `team.txt`, and `GITHUB_TOKEN` configuration is supported as the `default` profile.

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

Each `[TeamName]` header starts a group. Run one team or all teams; usernames before the first header go into `Ungrouped`.

---

## Metrics Reference

The report window is an inclusive range of calendar dates ending yesterday, calculated in **MYT (UTC+8)**. GitHub PR searches use date-only `created` ranges; commit timestamps are converted to MYT and filtered locally. Commit dates use the Git author timestamp, not the push or merge date.

| Report column | Exactly what it measures |
|---|---|
| **Total PRs** | PRs authored by the member in the organization and created within the window, regardless of whether they are open, closed, or merged. |
| **PRs / Working Day** | Total PRs divided by Monday-Friday dates in the window. Public holidays are still counted as working days. |
| **Merged PRs** | In-window PRs that are merged when the report runs. This is current status, not PRs whose merge date falls in the window. |
| **Merge Rate %** | Merged PRs divided by Total PRs. It is the merged share of the in-window PR cohort as of report time. |
| **Avg Merge Time (hrs)** | Mean of `merged_at - created_at` for merged PRs created in the window. It is elapsed calendar time; no eligible PRs yields `N/A`. |
| **Total Commits** | Unique fetched commit SHAs with zero or one parent and an author date in the window. Search results and PR branches are combined to capture commits from squash-merged and open PRs. |
| **Commits / Day** | Total Commits divided by distinct dates with at least one counted commit. This is intensity per active day, not per calendar day. |
| **Coding Days / Week** | Active commit dates normalized to a 7-day week; weekends count, zero-commit weeks are omitted, and partial window weeks are normalized. No commits yields `N/A`. |
| **Weekend Commits** | Unique non-merge commits whose author date in MYT falls on Saturday or Sunday within the window. |
| **Active Repos** | Distinct repositories represented in the collected commit data. Merge commits and records without an author date can make a repository active even though they are excluded from commit counts or day-based metrics. |
| **Reviews Given** | Number of PRs created in the window on which GitHub identifies the member as a reviewer. It counts PRs, not review submissions, and the review itself may be outside the window. |
| **PRs Commented On** | Number of other authors' PRs created in the window on which GitHub identifies the member as a commenter. It counts PRs, not comments, and the comment itself may be outside the window. |

The team-average row is the arithmetic mean of each member's displayed metric; it is not recalculated from team totals. `N/A` values are omitted from that average.

Records with no author date can affect Total Commits and Active Repos, but cannot be assigned to a coding day or weekend. `N/A` means a metric cannot be calculated, such as average merge time with no merged PRs or coding days per week with no commits.

GitHub Search returns at most 1,000 items per query. The tool warns when this limit is reached; metrics based on fetched items may then be incomplete. If a PR-branch request fails, a warning is printed and that PR's commit data may be partial.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Token invalid, missing scopes, or no org access | Check `repo` and `read:org` scopes and authorize the token for SAML SSO if required. |
| Organization not found | Check the organization name in the selected profile's `org.txt`. |
| All results are zero | Check the selected profile, member usernames, token scopes, and SSO authorization. |
| Missing `requests` or `openpyxl` | From the install directory, run `python3 -m pip install -r requirements.txt` (Mac) or `python -m pip install -r requirements.txt` (Windows). |
| Rate limit or slow run | Wait and retry, shorten the lookback, or run a smaller team. |
