#!/usr/bin/env python3
"""Pull PR, commit, and collaboration stats for team members from a GitHub org."""

import os
import re
import sys
import time
from collections import defaultdict, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

GITHUB_API = "https://api.github.com"

TEAM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team.txt")
ORG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "org.txt")

DEFAULT_LOOKBACK_DAYS = 90

MYT = timezone(timedelta(hours=8))

SEARCH_API_DELAY_SECONDS = 2.5
COMMIT_API_DELAY_SECONDS = 1.0
LINE_STATS_SAMPLE_SIZE = 5


def get_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("No GITHUB_TOKEN environment variable found.")
        print("Create a Classic token at https://github.com/settings/tokens")
        print("Required scopes: repo, read:org  (+ SSO authorize for your org)\n")
        token = input("Paste your GitHub token: ").strip()
        if not token:
            print("Error: No token provided.")
            sys.exit(1)
    return token


def validate_token(token, org):
    """Check token validity and required scopes. Returns True if OK, exits otherwise."""
    headers = {"Authorization": f"token {token}"}

    resp = requests.get(f"{GITHUB_API}/user", headers=headers, timeout=15)
    if resp.status_code == 401:
        print("\nError: Token is invalid or expired.")
        print("Create a new token at https://github.com/settings/tokens")
        sys.exit(1)

    scopes = resp.headers.get("X-OAuth-Scopes", "")
    scope_list = [s.strip() for s in scopes.split(",")]

    missing = []
    if "repo" not in scope_list:
        missing.append("repo")
    if "read:org" not in scope_list:
        missing.append("read:org")

    if missing:
        print(f"\nError: Token is missing required scope(s): {', '.join(missing)}")
        print(f"  Your token has: {scopes or '(none)'}")
        print("  Go to https://github.com/settings/tokens, edit your token,")
        print("  and enable: repo (top-level checkbox) + read:org")
        sys.exit(1)

    user = resp.json().get("login", "unknown")
    print(f"Token valid (authenticated as {user})")

    org_resp = requests.get(
        f"{GITHUB_API}/orgs/{org}/repos",
        headers={**headers, "Accept": "application/vnd.github.v3+json"},
        params={"per_page": 1},
        timeout=15,
    )
    if org_resp.status_code == 403 or org_resp.status_code == 404:
        print(f"\nWarning: Cannot access the {org} org.")
        print("  If the org uses SAML SSO, you need to authorize the token:")
        print("  1. Go to https://github.com/settings/tokens")
        print("  2. Click 'Configure SSO' next to your token")
        print(f"  3. Authorize it for {org}")
        print()
        proceed = input("Continue anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            sys.exit(1)

    return True


def load_org():
    if os.path.exists(ORG_FILE):
        with open(ORG_FILE) as f:
            org = f.read().strip()
            if org:
                return org
    org = input("Enter the GitHub organization name: ").strip()
    if not org:
        print("Error: No organization name provided.")
        sys.exit(1)
    with open(ORG_FILE, "w") as f:
        f.write(org + "\n")
    print(f"Saved org '{org}' to {ORG_FILE}")
    return org


def load_team_members():
    """Parse team.txt supporting both flat and grouped formats.

    Grouped format uses [TeamName] headers:
        [Payments]
        user1
        user2

        [BV]
        user3

    Returns (all_members, teams_dict) where teams_dict is an OrderedDict
    of team_name -> [usernames]. If no headers are present, returns a single
    "All" team with every member.
    """
    if not os.path.exists(TEAM_FILE):
        print(f"Error: Team file not found: {TEAM_FILE}")
        print("Create a team.txt file with one GitHub username per line.")
        sys.exit(1)

    teams = OrderedDict()
    current_team = None
    header_re = re.compile(r"^\[(.+)\]\s*$")

    with open(TEAM_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = header_re.match(line)
            if m:
                current_team = m.group(1)
                teams.setdefault(current_team, [])
            else:
                if current_team is None:
                    current_team = "Ungrouped"
                    teams.setdefault(current_team, [])
                teams[current_team].append(line)

    all_members = [u for members in teams.values() for u in members]
    if not all_members:
        print("Error: team.txt is empty. Add at least one GitHub username.")
        sys.exit(1)

    return all_members, teams


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

MAX_RATE_LIMIT_WAIT = 120


def _handle_rate_limit(resp, attempt, max_attempts):
    """Handle 403 rate-limit responses. Returns seconds to wait, or 0 to skip."""
    reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
    wait = max(reset_ts - int(time.time()), 5)
    if wait > MAX_RATE_LIMIT_WAIT:
        wait = MAX_RATE_LIMIT_WAIT
    print(f"    Rate limited (attempt {attempt}/{max_attempts}). Waiting {wait}s ...")
    time.sleep(wait)
    return wait


def _search_request(url, params, headers, accept=None, per_page=1):
    """Execute a GitHub search and return (total_count, items)."""
    req_headers = {**headers}
    if accept:
        req_headers["Accept"] = accept
    params = {**params, "per_page": per_page, "page": 1}

    for attempt in range(1, 4):
        try:
            resp = requests.get(url, params=params, headers=req_headers, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"    Request error (attempt {attempt}/3): {exc}")
            time.sleep(5 * attempt)
            continue

        if resp.status_code == 403:
            _handle_rate_limit(resp, attempt, 3)
            continue

        if resp.status_code == 422:
            return 0, []

        resp.raise_for_status()
        data = resp.json()
        return data.get("total_count", 0), data.get("items", [])

    print("    Max retries exceeded")
    return 0, []


def _search_count(endpoint, query, headers, accept=None):
    count, _ = _search_request(
        f"{GITHUB_API}{endpoint}", {"q": query}, headers, accept,
    )
    return count


def _search_items(endpoint, query, headers, accept=None, per_page=10):
    return _search_request(
        f"{GITHUB_API}{endpoint}", {"q": query}, headers, accept, per_page,
    )


def _search_all_items(endpoint, query, headers, accept=None):
    """Paginate through all search results (GitHub caps at 1000)."""
    url = f"{GITHUB_API}{endpoint}"
    all_items = []
    total_count = 0
    page = 1
    per_page = 100

    while True:
        req_headers = {**headers}
        if accept:
            req_headers["Accept"] = accept
        params = {"q": query, "per_page": per_page, "page": page}

        success = False
        for attempt in range(1, 4):
            try:
                resp = requests.get(url, params=params, headers=req_headers, timeout=30)
            except requests.exceptions.RequestException as exc:
                print(f"    Request error (attempt {attempt}/3): {exc}")
                time.sleep(5 * attempt)
                continue
            if resp.status_code == 403:
                _handle_rate_limit(resp, attempt, 3)
                continue
            if resp.status_code == 422:
                return 0, []
            resp.raise_for_status()
            success = True
            break

        if not success:
            print("    Max retries exceeded")
            break

        data = resp.json()
        total_count = data.get("total_count", 0)
        items = data.get("items", [])
        all_items.extend(items)

        if len(all_items) >= total_count or len(items) < per_page:
            break

        page += 1
        _delay()

    return total_count, all_items


def _delay():
    time.sleep(SEARCH_API_DELAY_SECONDS)


# ---------------------------------------------------------------------------
# Per-user query functions (each makes exactly one search API call)
# ---------------------------------------------------------------------------

def get_pr_count(username, since, headers, org):
    return _search_count(
        "/search/issues",
        f"type:pr author:{username} org:{org} created:>={since}",
        headers,
    )


def get_merged_prs(username, since, headers, org):
    """Return (merged_count, all merged PR items) with pagination."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{org} is:merged created:>={since}",
        headers,
    )


def get_unmerged_prs(username, since, headers, org):
    """Return (count, items) for all unmerged PRs (open, draft, and closed-without-merging)."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{org} is:unmerged created:>={since}",
        headers,
    )


def get_commits_with_items(username, since, headers, org):
    """Return (commit_count, all commit items) with pagination."""
    return _search_all_items(
        "/search/commits",
        f"author:{username} org:{org} committer-date:>={since}",
        headers,
        accept="application/vnd.github.cloak-preview+json",
    )


def get_reviews_given(username, since, headers, org):
    return _search_count(
        "/search/issues",
        f"type:pr reviewed-by:{username} org:{org} created:>={since}",
        headers,
    )


def get_prs_commented_on(username, since, headers, org):
    """PRs authored by others where this user left comments."""
    return _search_count(
        "/search/issues",
        f"type:pr commenter:{username} -author:{username} org:{org} created:>={since}",
        headers,
    )


# ---------------------------------------------------------------------------
# Derived-metric helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def compute_avg_merge_hours(merged_items):
    """Average hours from PR creation to merge, based on fetched items."""
    durations = []
    for item in merged_items:
        created = _parse_iso(item["created_at"])
        merged_at = (item.get("pull_request") or {}).get("merged_at")
        if not merged_at:
            merged_at = item.get("closed_at")
        if not merged_at:
            continue
        hours = (_parse_iso(merged_at) - created).total_seconds() / 3600
        if hours >= 0:
            durations.append(hours)
    return round(sum(durations) / len(durations), 1) if durations else None


def compute_coding_day_stats(commit_items, start_date, end_date):
    """Return (avg_coding_days_per_week, total_coding_days).

    avg_coding_days_per_week follows Flow's definition: all days
    (including weekends) count, merge commits are excluded, zero-commit
    weeks are excluded, partial weeks use Flow's normalization.

    total_coding_days is the raw count of unique days with at least one
    non-merge commit (used to derive commits-per-coding-day).
    """
    coding_dates = set()
    for item in commit_items:
        if len(item.get("parents", [])) > 1:
            continue
        committer = item.get("commit", {}).get("committer", {})
        date_str = committer.get("date")
        if not date_str:
            continue
        dt = _parse_iso(date_str).astimezone(MYT).date()
        if start_date <= dt <= end_date:
            coding_dates.add(dt)

    if not coding_dates:
        return None, 0

    period_days_by_week = defaultdict(int)
    current = start_date
    while current <= end_date:
        period_days_by_week[current.isocalendar()[:2]] += 1
        current += timedelta(days=1)

    coding_days_by_week = defaultdict(set)
    for d in coding_dates:
        coding_days_by_week[d.isocalendar()[:2]].add(d)

    active_weeks = set(coding_days_by_week.keys())
    total_coding_days = sum(len(days) for days in coding_days_by_week.values())
    total_days = sum(period_days_by_week[wk] for wk in active_weeks)

    if total_days == 0:
        return None, 0

    avg = (total_coding_days / total_days) * min(7, total_days)
    return round(avg, 1), total_coding_days


def compute_weekend_commits(commit_items, start_date, end_date):
    """Return (total_weekend_commits, avg_commits_per_weekend) in MYT."""
    weekend_commit_count = 0
    for item in commit_items:
        committer = item.get("commit", {}).get("committer", {})
        date_str = committer.get("date")
        if not date_str:
            continue
        dt = _parse_iso(date_str).astimezone(MYT).date()
        if dt.weekday() >= 5:
            weekend_commit_count += 1

    weekends = 0
    current = start_date
    while current <= end_date:
        if current.weekday() == 5:
            weekends += 1
        current += timedelta(days=1)
    weekends = max(weekends, 1)

    avg = round(weekend_commit_count / weekends, 2) if weekend_commit_count else 0.0
    return weekend_commit_count, avg


def count_active_repos(commit_items):
    return len({
        item["repository"]["full_name"]
        for item in commit_items
        if "repository" in item
    })


PR_BRANCH_WORKERS = 8


def fetch_pr_branch_commits(pr_items, headers, username):
    """Fetch commit objects from PR branches authored by ``username``.

    GitHub's commit search only indexes default-branch commits. For
    squash-merged PRs the individual branch commits are lost, and
    open/draft PR branches are never on the default branch. This
    retrieves them via the PR commits endpoint.

    Uses a thread pool for concurrent fetching (up to PR_BRANCH_WORKERS
    parallel requests).

    Returns a list of commit dicts (deduplicated by SHA). Each dict is
    compatible with the search-commits format: it contains at least
    ``sha``, ``commit.committer.date``, ``parents``, and a synthesised
    ``repository.full_name`` extracted from the PR URL.
    """
    total = len(pr_items)
    if not total:
        return []
    print(f"  Fetching PR branch commits ({total} PRs) ...")

    uname = username.lower()

    def _fetch_one(item):
        pr_url = (item.get("pull_request") or {}).get("url")
        if not pr_url:
            return []
        repo_match = re.match(r".*/repos/([^/]+/[^/]+)/pulls/", pr_url)
        repo_name = repo_match.group(1) if repo_match else None
        try:
            resp = requests.get(
                f"{pr_url}/commits",
                headers=headers,
                params={"per_page": 250},
                timeout=30,
            )
            if resp.status_code == 200:
                result = []
                for c in resp.json():
                    author = c.get("author")
                    if author is not None and author.get("login", "").lower() != uname:
                        continue
                    if repo_name:
                        c.setdefault("repository", {})["full_name"] = repo_name
                    result.append(c)
                return result
        except Exception:
            pass
        return []

    commits = []
    seen_shas = set()
    with ThreadPoolExecutor(max_workers=PR_BRANCH_WORKERS) as pool:
        for batch in pool.map(_fetch_one, pr_items):
            for c in batch:
                sha = c.get("sha")
                if sha and sha not in seen_shas:
                    seen_shas.add(sha)
                    commits.append(c)
    return commits


def fetch_pr_response_times(pr_items, headers, username):
    """Compute reaction time and time-to-first-comment for a user's PRs.

    For each PR authored by ``username``, fetches reviews and issue comments
    to find the earliest response from someone other than the author.

    Returns (avg_reaction_hrs, avg_first_comment_hrs) as floats rounded to 1
    decimal, or None when no data is available.  Reaction time considers both
    reviews and comments; time-to-first-comment considers only comments.
    """
    if not pr_items:
        return None, None

    uname = username.lower()
    print(f"  Fetching PR response times ({len(pr_items)} PRs) ...")

    def _parse_iso(ts):
        if not ts:
            return None
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def _fetch_one(item):
        pr_url = (item.get("pull_request") or {}).get("url")
        created_str = item.get("created_at")
        if not pr_url or not created_str:
            return None, None
        created = _parse_iso(created_str)
        if created is None:
            return None, None

        first_review_dt = None
        first_comment_dt = None

        try:
            resp = requests.get(
                f"{pr_url}/reviews",
                headers=headers,
                params={"per_page": 100},
                timeout=30,
            )
            if resp.status_code == 200:
                for rv in resp.json():
                    reviewer = (rv.get("user") or {}).get("login", "").lower()
                    if reviewer == uname:
                        continue
                    submitted = _parse_iso(rv.get("submitted_at"))
                    if submitted and (first_review_dt is None or submitted < first_review_dt):
                        first_review_dt = submitted
                        break
        except Exception:
            pass

        issue_url = pr_url.replace("/pulls/", "/issues/")
        try:
            resp = requests.get(
                f"{issue_url}/comments",
                headers=headers,
                params={"per_page": 100},
                timeout=30,
            )
            if resp.status_code == 200:
                for cm in resp.json():
                    commenter = (cm.get("user") or {}).get("login", "").lower()
                    if commenter == uname:
                        continue
                    commented = _parse_iso(cm.get("created_at"))
                    if commented and (first_comment_dt is None or commented < first_comment_dt):
                        first_comment_dt = commented
                        break
        except Exception:
            pass

        reaction_dt = None
        for dt in (first_review_dt, first_comment_dt):
            if dt and (reaction_dt is None or dt < reaction_dt):
                reaction_dt = dt

        reaction_hrs = (reaction_dt - created).total_seconds() / 3600 if reaction_dt else None
        comment_hrs = (first_comment_dt - created).total_seconds() / 3600 if first_comment_dt else None
        return reaction_hrs, comment_hrs

    reaction_hours = []
    comment_hours = []
    with ThreadPoolExecutor(max_workers=PR_BRANCH_WORKERS) as pool:
        for r_hrs, c_hrs in pool.map(_fetch_one, pr_items):
            if r_hrs is not None:
                reaction_hours.append(r_hrs)
            if c_hrs is not None:
                comment_hours.append(c_hrs)

    avg_reaction = round(sum(reaction_hours) / len(reaction_hours), 1) if reaction_hours else None
    avg_comment = round(sum(comment_hours) / len(comment_hours), 1) if comment_hours else None
    return avg_reaction, avg_comment


def fetch_line_stats_sample(commit_items, headers):
    """Hit the Commits API for a small sample to get additions / deletions.

    Returns (total_additions, total_deletions, sample_size).
    """
    additions = deletions = sampled = 0
    for item in commit_items[:LINE_STATS_SAMPLE_SIZE]:
        repo = (item.get("repository") or {}).get("full_name")
        sha = item.get("sha")
        if not repo or not sha:
            continue
        try:
            resp = requests.get(
                f"{GITHUB_API}/repos/{repo}/commits/{sha}",
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 200:
                stats = resp.json().get("stats", {})
                additions += stats.get("additions", 0)
                deletions += stats.get("deletions", 0)
                sampled += 1
            time.sleep(COMMIT_API_DELAY_SECONDS)
        except Exception:
            continue
    return additions, deletions, sampled


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def count_working_days(start_date, end_date):
    """Count weekdays (Mon-Fri) between two dates, inclusive."""
    days = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Excel + console output helpers
# ---------------------------------------------------------------------------

_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_ALT_ROW_FILL = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
_THIN_BORDER = Border(
    bottom=Side(style="thin", color="B4C6E7"),
)

_COLUMNS = [
    ("Username",                  "username",                  16),
    ("Total PRs",                 "total_prs",                 10),
    ("PRs / Working Day",         "prs_per_working_day",       16),
    ("Merged PRs",                "merged_prs",                11),
    ("Merge Rate %",              "merge_rate_pct",            12),
    ("Avg Merge Time (hrs)",      "avg_merge_time_hrs",        20),
    ("Total Commits",             "total_commits",             13),
    ("Commits / Day",             "commits_per_coding_day",    14),
    ("Coding Days / Week",        "avg_coding_days_per_week",  18),
    ("Weekend Commits",           "weekend_commits",           16),
    ("Active Repos",              "active_repos",              12),
    ("Reaction Time (hrs)",       "avg_reaction_time_hrs",     18),
    ("Time to 1st Comment (hrs)", "avg_first_comment_hrs",     22),
    ("Reviews Given",             "reviews_given",             14),
    ("PRs Commented On",          "prs_commented_on",          17),
    ("Avg Lines Added / Commit",  "avg_additions_per_commit",  22),
    ("Avg Lines Removed / Commit","avg_deletions_per_commit",  24),
]


_TEAM_AVG_KEYS = [
    "prs_per_working_day",
    "merge_rate_pct",
    "avg_merge_time_hrs",
    "commits_per_coding_day",
    "avg_coding_days_per_week",
    "weekend_commits",
    "avg_reaction_time_hrs",
    "avg_first_comment_hrs",
    "reviews_given",
    "prs_commented_on",
    "avg_additions_per_commit",
    "avg_deletions_per_commit",
]


def _compute_team_averages(rows):
    """Return a dict with team-average values for the keys in _TEAM_AVG_KEYS.

    Keys not in _TEAM_AVG_KEYS are left blank. None values are excluded from
    the average (so a user with None merge time doesn't drag it down).
    """
    if not rows:
        return {}
    avgs = {"username": "TEAM AVERAGE"}
    for key in _TEAM_AVG_KEYS:
        vals = [r[key] for r in rows if r.get(key) is not None]
        if vals:
            avgs[key] = round(sum(vals) / len(vals), 1)
        else:
            avgs[key] = None
    for _, key, _ in _COLUMNS:
        if key not in avgs:
            avgs[key] = ""
    return avgs


_TEAM_AVG_FONT = Font(bold=True, color="FFFFFF", size=11)
_TEAM_AVG_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")


def _write_stats_sheet(ws, rows, team_avg=None):
    """Write a formatted stats table into an openpyxl worksheet."""
    for col_idx, (title, _, width) in enumerate(_COLUMNS, 1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    for row_idx, r in enumerate(rows, 2):
        for col_idx, (_, key, _) in enumerate(_COLUMNS, 1):
            val = r.get(key)
            if val is None:
                val = ""
            ws.cell(row=row_idx, column=col_idx, value=val)

        if row_idx % 2 == 0:
            for col_idx in range(1, len(_COLUMNS) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = _ALT_ROW_FILL

        for col_idx in range(1, len(_COLUMNS) + 1):
            ws.cell(row=row_idx, column=col_idx).border = _THIN_BORDER

    if team_avg:
        avg_row = len(rows) + 3
        for col_idx, (_, key, _) in enumerate(_COLUMNS, 1):
            val = team_avg.get(key)
            if val is None:
                val = ""
            cell = ws.cell(row=avg_row, column=col_idx, value=val)
            cell.font = _TEAM_AVG_FONT
            cell.fill = _TEAM_AVG_FILL
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def _fmt_val(val, fmt="", prefix="", suffix="", na="N/A"):
    """Format a value for console display, handling None gracefully."""
    if val is None or val == "":
        return na
    return f"{prefix}{val:{fmt}}{suffix}"


def _print_console_tables(results, team_avg=None):
    """Print the two summary tables to stdout, with optional team average."""
    print(f"\n{'─' * 110}")
    print("ACTIVITY")
    hdr1 = (f"{'Username':<20} {'PRs':>6} {'PRs/Day':>8} {'Merged%':>8} "
            f"{'Commits':>8} {'Cmts/Day':>9} {'Coding Days':>12} "
            f"{'Wknd Cmts':>10}")
    print(hdr1)
    print("─" * len(hdr1))

    def _print_activity_row(r):
        cd_str = _fmt_val(r.get("avg_coding_days_per_week"))
        prs = _fmt_val(r.get("total_prs"), na="")
        cmts = _fmt_val(r.get("total_commits"), na="")
        wknd = _fmt_val(r.get("weekend_commits"), na="")
        print(f"{r['username']:<20} "
              f"{prs:>6} "
              f"{_fmt_val(r.get('prs_per_working_day')):>8} "
              f"{_fmt_val(r.get('merge_rate_pct'), suffix='%'):>8} "
              f"{cmts:>8} "
              f"{_fmt_val(r.get('commits_per_coding_day')):>9} "
              f"{cd_str:>12} "
              f"{wknd:>10}")

    for r in results:
        _print_activity_row(r)
    if team_avg:
        print("─" * len(hdr1))
        _print_activity_row(team_avg)

    print(f"\n{'─' * 120}")
    print("COLLABORATION & QUALITY")
    hdr2 = (f"{'Username':<20} {'Reviews':>8} {'Commented':>10} "
            f"{'Reaction':>9} {'1st Cmt':>8} "
            f"{'Merge Time':>11} {'Repos':>6} {'Lines Added':>12} {'Lines Removed':>14}")
    print(hdr2)
    print("─" * len(hdr2))

    def _print_collab_row(r):
        m = r.get("avg_merge_time_hrs")
        merge_str = f"{m}h" if m is not None else "N/A"
        react_str = _fmt_val(r.get("avg_reaction_time_hrs"), suffix="h")
        cmt_str = _fmt_val(r.get("avg_first_comment_hrs"), suffix="h")
        add_str = _fmt_val(r.get("avg_additions_per_commit"), prefix="+")
        del_str = _fmt_val(r.get("avg_deletions_per_commit"), prefix="-")
        reviews = _fmt_val(r.get("reviews_given"), na="")
        commented = _fmt_val(r.get("prs_commented_on"), na="")
        repos = _fmt_val(r.get("active_repos"), na="")
        print(f"{r['username']:<20} "
              f"{reviews:>8} "
              f"{commented:>10} "
              f"{react_str:>9} "
              f"{cmt_str:>8} "
              f"{merge_str:>11} "
              f"{repos:>6} "
              f"{add_str:>12} "
              f"{del_str:>14}")

    for r in results:
        _print_collab_row(r)
    if team_avg:
        print("─" * len(hdr2))
        _print_collab_row(team_avg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_lookback_days():
    """Get lookback days from CLI arg or interactive prompt."""
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
            if days > 0:
                return days
        except ValueError:
            pass

    while True:
        raw = input(f"How many days to look back? [{DEFAULT_LOOKBACK_DAYS}]: ").strip()
        if not raw:
            return DEFAULT_LOOKBACK_DAYS
        try:
            days = int(raw)
            if days > 0:
                return days
            print("  Please enter a positive number.")
        except ValueError:
            print("  Please enter a valid number.")


def choose_team(teams):
    """Prompt the user to run for all teams or a specific one.

    Returns (run_members, run_teams) where run_teams is the subset of
    teams to include in the Excel output.
    """
    team_names = list(teams.keys())
    all_members = [u for members in teams.values() for u in members]

    if len(team_names) <= 1:
        return all_members, teams

    print(f"\nTeams found: {len(team_names)}")
    print(f"  0. All teams ({len(all_members)} members)")
    for i, name in enumerate(team_names, 1):
        print(f"  {i}. {name} ({len(teams[name])} members)")

    while True:
        raw = input(f"\nRun report for which team? [0 = All]: ").strip()
        if not raw or raw == "0":
            return all_members, teams
        try:
            choice = int(raw)
            if 1 <= choice <= len(team_names):
                picked = team_names[choice - 1]
                picked_teams = OrderedDict([(picked, teams[picked])])
                return teams[picked], picked_teams
            print(f"  Please enter a number between 0 and {len(team_names)}.")
        except ValueError:
            print("  Please enter a valid number.")


def main():
    token = get_token()
    org = load_org()
    validate_token(token, org)
    _, teams = load_team_members()
    team_members, run_teams = choose_team(teams)
    lookback_days = get_lookback_days()

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    now = datetime.now(MYT)
    since = now - timedelta(days=lookback_days)
    since_date = since.strftime("%Y-%m-%d")
    today_date = now.strftime("%Y-%m-%d")
    working_days = count_working_days(since.date(), now.date())

    total = len(team_members)
    scope_label = "All teams" if len(run_teams) > 1 else list(run_teams.keys())[0]
    search_calls_per_user = 6
    avg_prs_per_user = 100
    pr_calls_per_pr = 3  # branch commits + reviews + comments
    pr_fetch_seconds = avg_prs_per_user * pr_calls_per_pr / PR_BRANCH_WORKERS
    est_min = round(
        (total * search_calls_per_user * SEARCH_API_DELAY_SECONDS
         + total * LINE_STATS_SAMPLE_SIZE * COMMIT_API_DELAY_SECONDS
         + total * pr_fetch_seconds) / 60,
        1,
    )

    print(f"\nGitHub Stats for {total} team members  ({scope_label})")
    print(f"Org:            {org}")
    print(f"Period:         {since_date} → {today_date}  "
          f"({lookback_days} calendar days, {working_days} working days)")
    print(f"Estimated time: ~{est_min} min")
    print("=" * 90)

    results = []

    for i, username in enumerate(team_members, 1):
        print(f"\n[{i}/{total}] {username}")

        print("  Fetching search data ...", end="", flush=True)
        pr_count = get_pr_count(username, since_date, headers, org)
        _delay()

        merged_count, merged_items = get_merged_prs(username, since_date, headers, org)
        _delay()

        _, unmerged_items = get_unmerged_prs(username, since_date, headers, org)
        _delay()

        commit_count, commit_items = get_commits_with_items(username, since_date, headers, org)
        _delay()

        reviews_given = get_reviews_given(username, since_date, headers, org)
        _delay()

        prs_commented = get_prs_commented_on(username, since_date, headers, org)
        if i < total:
            _delay()
        print(f" done ({pr_count} PRs, {commit_count} commits)")

        # Fetch PR branch commits and merge with search-API commits
        all_pr_items = merged_items + unmerged_items
        pr_branch_commits = fetch_pr_branch_commits(
            all_pr_items, headers, username,
        )
        search_shas = {item.get("sha") for item in commit_items}
        unique_pr_commits = [
            c for c in pr_branch_commits if c.get("sha") not in search_shas
        ]
        all_commit_items = commit_items + unique_pr_commits
        total_commit_count = commit_count + len(unique_pr_commits)

        # Derived metrics
        prs_per_wd = round(pr_count / working_days, 2) if working_days else 0
        merge_rate = round(merged_count / pr_count * 100, 1) if pr_count else 0.0
        avg_merge_hrs = compute_avg_merge_hours(merged_items)
        avg_coding_days, total_coding_days = compute_coding_day_stats(
            all_commit_items, since.date(), now.date(),
        )
        commits_per_cd = (
            round(total_commit_count / total_coding_days, 1)
            if total_coding_days else 0
        )
        wknd_commits, _ = compute_weekend_commits(
            all_commit_items, since.date(), now.date(),
        )
        active_repos = count_active_repos(all_commit_items)

        # PR response times (reaction time + time to first comment)
        avg_reaction_hrs, avg_first_comment_hrs = fetch_pr_response_times(
            all_pr_items, headers, username,
        )

        line_stats_pool = unique_pr_commits if unique_pr_commits else commit_items
        additions = deletions = sampled = 0
        if line_stats_pool:
            additions, deletions, sampled = fetch_line_stats_sample(
                line_stats_pool, headers,
            )
        avg_add = round(additions / sampled) if sampled else 0
        avg_del = round(deletions / sampled) if sampled else 0

        result = {
            "username": username,
            "total_prs": pr_count,
            "prs_per_working_day": prs_per_wd,
            "merged_prs": merged_count,
            "merge_rate_pct": merge_rate,
            "avg_merge_time_hrs": avg_merge_hrs,
            "total_commits": total_commit_count,
            "commits_per_coding_day": commits_per_cd,
            "avg_coding_days_per_week": avg_coding_days,
            "weekend_commits": wknd_commits,
            "active_repos": active_repos,
            "avg_reaction_time_hrs": avg_reaction_hrs,
            "avg_first_comment_hrs": avg_first_comment_hrs,
            "reviews_given": reviews_given,
            "prs_commented_on": prs_commented,
            "avg_additions_per_commit": avg_add,
            "avg_deletions_per_commit": avg_del,
        }
        results.append(result)

        merge_str = f"{avg_merge_hrs}h" if avg_merge_hrs is not None else "N/A"
        coding_str = f"{avg_coding_days}" if avg_coding_days is not None else "N/A"
        reaction_str = f"{avg_reaction_hrs}h" if avg_reaction_hrs is not None else "N/A"
        comment_str = f"{avg_first_comment_hrs}h" if avg_first_comment_hrs is not None else "N/A"
        print(f"  Activity:  PRs: {pr_count} ({prs_per_wd}/working day, {merge_rate}% merged) "
              f"| Commits: {total_commit_count} ({commits_per_cd}/day) "
              f"| Coding days/week: {coding_str} "
              f"| Weekend commits: {wknd_commits}")
        print(f"  Quality:   Merge time: {merge_str} "
              f"| Active repos: {active_repos} "
              f"| Lines/commit: +{avg_add}/-{avg_del}")
        print(f"  Collab:    Reviews: {reviews_given} "
              f"| PRs commented: {prs_commented} "
              f"| Reaction time: {reaction_str} "
              f"| 1st comment: {comment_str}")

    results.sort(key=lambda r: r["total_commits"], reverse=True)
    results_by_user = {r["username"]: r for r in results}

    # ── Excel export ────────────────────────────────────────────────────────
    output_file = f"github_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb = Workbook()

    sheet_sets = []
    if len(run_teams) > 1:
        sheet_sets.append(("All", results))
    for team_name, members in run_teams.items():
        team_results = [results_by_user[u] for u in members if u in results_by_user]
        team_results.sort(key=lambda r: r["total_commits"], reverse=True)
        sheet_sets.append((team_name, team_results))

    for idx, (sheet_name, sheet_results) in enumerate(sheet_sets):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = sheet_name[:31]
        team_avg = _compute_team_averages(sheet_results) if len(sheet_results) > 1 else None
        _write_stats_sheet(ws, sheet_results, team_avg=team_avg)

    wb.save(output_file)

    print("\n" + "=" * 90)
    sheets_desc = ", ".join(name for name, _ in sheet_sets)
    print(f"Excel exported → {output_file}  (sheets: {sheets_desc})")

    # ── Console tables ──────────────────────────────────────────────────────
    overall_avg = _compute_team_averages(results) if len(results) > 1 else None
    _print_console_tables(results, team_avg=overall_avg)


if __name__ == "__main__":
    main()
