#!/usr/bin/env python3
"""Pull PR, commit, and collaboration stats for team members from a GitHub org."""

import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

import requests
from openpyxl import Workbook

from config import (
    GITHUB_API,
    SEARCH_API_DELAY_SECONDS,
    PR_BRANCH_WORKERS,
    DEFAULT_LOOKBACK_DAYS,
    config,
)
from github_api import (
    delay,
    get_pr_count,
    get_merged_prs,
    get_unmerged_prs,
    get_old_merged_prs,
    get_old_open_prs,
    get_commits_with_items,
    get_reviews_given,
    get_prs_commented_on,
    fetch_pr_branch_commits,
    set_api_base,
)
from metrics import (
    MYT,
    parse_iso,
    compute_avg_merge_hours,
    compute_coding_day_stats,
    compute_weekend_commits,
    count_active_repos,
    count_working_days,
)
from output import (
    compute_team_averages,
    write_stats_sheet,
    print_console_tables,
)
from logger import info, warning, error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEAM_FILE = os.path.join(SCRIPT_DIR, config.team_file_name)
ORG_FILE = os.path.join(SCRIPT_DIR, config.org_file_name)
API_FILE = os.path.join(SCRIPT_DIR, config.api_file_name)
PROFILES_DIR = os.path.join(SCRIPT_DIR, config.profiles_dir_name)

# Type aliases for better type safety
Headers = Dict[str, str]
Teams = OrderedDict[str, List[str]]
UserRow = Dict[str, Any]  # User metrics row
GitHubItem = Dict[str, Any]
CommitItem = Dict[str, Any]


# ---------------------------------------------------------------------------
# Setup helpers (token, org, team)
# ---------------------------------------------------------------------------

def _token_env_name(profile_name: str) -> str:
    """Return the environment variable name for a profile token."""
    normalized = re.sub(r"[^A-Za-z0-9]", "_", profile_name).upper()
    return f"GITHUB_TOKEN_{normalized}"


def get_token(profile_name: str, use_legacy_token: bool) -> str:
    """Read a profile token from the environment, or prompt interactively."""
    token = os.environ.get(_token_env_name(profile_name))
    if not token and use_legacy_token:
        token = os.environ.get("GITHUB_TOKEN")
    if not token:
        info(f"No token environment variable found for profile '{profile_name}'.")
        info("Create a Classic token at https://github.com/settings/tokens")
        info("Required scopes: repo, read:org  (+ SSO authorize for your org)\n")
        token = input(f"Paste the GitHub token for '{profile_name}': ").strip()
        if not token:
            error("Error: No token provided.")
            sys.exit(1)
    return token


def validate_token(token: str, org: str, api_base: str) -> None:
    """Check token validity, required scopes, and org access. Exits on failure."""
    if not token or not token.strip():
        error("Error: GitHub token is empty or invalid.")
        sys.exit(1)

    if not org or not org.strip():
        error("Error: GitHub organization name is empty or invalid.")
        sys.exit(1)

    headers = {"Authorization": f"token {token}"}

    try:
        resp = requests.get(f"{api_base}/user", headers=headers, timeout=config.request_timeout_seconds)
    except requests.exceptions.RequestException as exc:
        error(f"Error: Failed to connect to GitHub API: {exc}")
        sys.exit(1)

    if resp.status_code == 401:
        error("\nError: Token is invalid or expired.")
        error("Create a new token at https://github.com/settings/tokens")
        error("Required scopes: repo, read:org (+ SSO authorize for your org)")
        sys.exit(1)

    scopes = resp.headers.get("X-OAuth-Scopes", "")
    scope_list = [s.strip() for s in scopes.split(",")]

    missing = []
    if "repo" not in scope_list:
        missing.append("repo")
    if "read:org" not in scope_list:
        missing.append("read:org")

    if missing:
        error(f"\nError: Token is missing required scope(s): {', '.join(missing)}")
        error(f"  Your token has: {scopes or '(none)'}")
        error("  Go to https://github.com/settings/tokens, edit your token,")
        error("  and enable: repo (top-level checkbox) + read:org")
        sys.exit(1)

    user = resp.json().get("login", "unknown")
    info(f"Token valid (authenticated as {user})")

    try:
        org_resp = requests.get(
            f"{api_base}/orgs/{org}/repos",
            headers={**headers, "Accept": "application/vnd.github.v3+json"},
            params={"per_page": 1},
            timeout=config.request_timeout_seconds,
        )
    except requests.exceptions.RequestException as exc:
        error(f"Error: Failed to check organization access: {exc}")
        sys.exit(1)

    if org_resp.status_code == 404:
        error(f"\nError: Organization '{org}' not found.")
        error("  Check the organization name and ensure it exists.")
        sys.exit(1)
    elif org_resp.status_code == 403:
        warning(f"\nWarning: Cannot access the {org} organization.")
        warning("  If the org uses SAML SSO, you need to authorize the token:")
        warning("  1. Go to https://github.com/settings/tokens")
        warning("  2. Click 'Configure SSO' next to your token")
        warning(f"  3. Authorize it for {org}")
        info("")
        proceed = input("Continue anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            sys.exit(1)
    elif not org_resp.ok:
        error(f"Error: Unexpected response when checking org access: {org_resp.status_code}")
        error(f"Response: {org_resp.text[:200]}")
        sys.exit(1)


def load_org(org_file: str) -> str:
    """Load org name from org.txt, or prompt and save it."""
    if os.path.exists(org_file):
        with open(org_file) as fh:
            org = fh.read().strip()
            if org:
                info(f"Organization: {org}")
                return org
    org = input("Enter the GitHub organization name: ").strip()
    if not org:
        error("Error: No organization name provided.")
        sys.exit(1)
    os.makedirs(os.path.dirname(org_file), exist_ok=True)
    with open(org_file, "w") as fh:
        fh.write(org + "\n")
    info(f"Saved org '{org}' to {org_file}")
    return org


def _create_team_interactive(team_file: str) -> Tuple[List[str], Teams]:
    """Walk the user through creating teams and members interactively."""
    info("No team file found. Let's set up your teams now.\n")
    info("You'll enter team names first, then GitHub usernames for each team.")
    info("Press Enter on an empty line to finish each step.\n")

    teams: Teams = OrderedDict()
    total_members = 0

    while True:
        team_name = input("  Team name (empty to finish adding teams): ").strip()
        if not team_name:
            break

        teams[team_name] = []
        info(f"    Adding members to {team_name}...")

        while True:
            username = input("      GitHub username (empty to finish this team): ").strip()
            if not username:
                break
            teams[team_name].append(username)
            total_members += 1

        info(f"    Added {len(teams[team_name])} member(s) to {team_name}.\n")

    if total_members == 0:
        error("Error: No team members added.")
        sys.exit(1)

    with open(team_file, "w") as fh:
        for tname, members in teams.items():
            fh.write(f"[{tname}]\n")
            for m in members:
                fh.write(f"{m}\n")
            fh.write("\n")

    info(f"Saved {total_members} member(s) across {len(teams)} team(s) to {team_file}\n")

    all_members = [u for members in teams.values() for u in members]
    return all_members, teams


def load_team_members(team_file: str) -> Tuple[List[str], Teams]:
    """Parse team.txt or create it interactively if missing.

    Grouped format uses [TeamName] headers. Returns (all_members, teams_dict)
    where teams_dict is an OrderedDict of team_name -> [usernames].
    """
    if not os.path.exists(team_file):
        return _create_team_interactive(team_file)

    teams: Teams = OrderedDict()
    current_team = None
    header_re = re.compile(r"^\[(.+)\]\s*$")

    with open(team_file) as fh:
        for line in fh:
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
        info("Error: team.txt is empty. Add at least one GitHub username.")
        sys.exit(1)

    return all_members, teams


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def get_lookback_days() -> int:
    """Get lookback days from CLI arg or interactive prompt."""
    args = list(sys.argv[1:])
    if "--profile" in args:
        profile_index = args.index("--profile")
        del args[profile_index:profile_index + 2]

    if args:
        try:
            days = int(args[0])
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
            info("  Please enter a positive number.")
        except ValueError:
            info("  Please enter a valid number.")


def _profile_paths(profile_name: str) -> Tuple[str, str, str]:
    """Return the org, team, and API files for a named profile."""
    profile_dir = os.path.join(PROFILES_DIR, profile_name)
    return (
        os.path.join(profile_dir, config.org_file_name),
        os.path.join(profile_dir, config.team_file_name),
        os.path.join(profile_dir, config.api_file_name),
    )


def choose_profile() -> Tuple[str, str, str, str, bool]:
    """Choose a profile, falling back to the legacy single-org files."""
    profile_arg = None
    for index, arg in enumerate(sys.argv):
        if arg == "--profile" and index + 1 < len(sys.argv):
            profile_arg = sys.argv[index + 1]
            break

    profile_names = sorted(
        name for name in os.listdir(PROFILES_DIR)
        if os.path.isdir(os.path.join(PROFILES_DIR, name))
        and not name.startswith(".")
    ) if os.path.isdir(PROFILES_DIR) else []

    if (os.path.exists(ORG_FILE) or os.path.exists(TEAM_FILE)) and "default" not in profile_names:
        profile_names.insert(0, "default")

    if not profile_names:
        return "default", ORG_FILE, TEAM_FILE, API_FILE, True

    if profile_arg:
        if profile_arg not in profile_names:
            error(f"Error: Profile '{profile_arg}' was not found in {PROFILES_DIR}.")
            sys.exit(1)
        selected = profile_arg
    elif len(profile_names) == 1:
        selected = profile_names[0]
    else:
        info("\nProfiles found:")
        for index, name in enumerate(profile_names, 1):
            info(f"  {index}. {name}")
        while True:
            raw = input("\nRun report for which profile? [1]: ").strip() or "1"
            try:
                choice = int(raw)
                if 1 <= choice <= len(profile_names):
                    selected = profile_names[choice - 1]
                    break
            except ValueError:
                pass
            info(f"  Please enter a number between 1 and {len(profile_names)}.")

    if selected == "default":
        return selected, ORG_FILE, TEAM_FILE, API_FILE, True

    org_file, team_file, api_file = _profile_paths(selected)
    return selected, org_file, team_file, api_file, False


def load_api_base(api_file: str) -> str:
    """Load a profile API base, defaulting to GitHub.com."""
    if not api_file:
        return GITHUB_API
    if not os.path.exists(api_file):
        return GITHUB_API
    with open(api_file) as fh:
        api_base = fh.read().strip().rstrip("/")
    if not api_base:
        return GITHUB_API

    parsed = urlsplit(api_base)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        error("Error: API base URL must be an HTTPS URL without credentials, query parameters, or fragments.")
        sys.exit(1)
    return api_base


def choose_team(teams: Teams) -> Tuple[List[str], Teams]:
    """Prompt the user to run for all teams or a specific one.

    Returns (run_members, run_teams) where run_teams is the subset of
    teams to include in the Excel output.
    """
    team_names = list(teams.keys())
    all_members = [u for members in teams.values() for u in members]

    if len(team_names) <= 1:
        return all_members, teams

    info(f"\nTeams found: {len(team_names)}")
    info(f"  0. All teams ({len(all_members)} members)")
    for i, name in enumerate(team_names, 1):
        info(f"  {i}. {name} ({len(teams[name])} members)")

    while True:
        raw = input("\nRun report for which team? [0 = All]: ").strip()
        if not raw or raw == "0":
            return all_members, teams
        try:
            choice = int(raw)
            if 1 <= choice <= len(team_names):
                picked = team_names[choice - 1]
                picked_teams = OrderedDict([(picked, teams[picked])])
                return teams[picked], picked_teams
            info(f"  Please enter a number between 0 and {len(team_names)}.")
        except ValueError:
            info("  Please enter a valid number.")


# ---------------------------------------------------------------------------
# Per-user data collection
# ---------------------------------------------------------------------------

def _collect_api_data(
    username: str, since_date: str, headers: Headers, org: str, index: int, total: int
) -> Tuple[int, List[GitHubItem], List[GitHubItem], List[GitHubItem], List[GitHubItem],
           int, List[GitHubItem], int, int]:
    """Collect raw data from GitHub API for a user."""
    print("  Fetching search data ...", end="", flush=True)
    pr_count = get_pr_count(username, since_date, headers, org)
    delay()

    merged_count, merged_items = get_merged_prs(username, since_date, headers, org)
    delay()

    _, unmerged_items = get_unmerged_prs(username, since_date, headers, org)
    delay()

    commit_count, commit_items = get_commits_with_items(username, since_date, headers, org)
    delay()

    reviews_given = get_reviews_given(username, since_date, headers, org)
    delay()

    prs_commented = get_prs_commented_on(username, since_date, headers, org)
    delay()

    _, old_merged_items = get_old_merged_prs(username, since_date, headers, org)
    delay()

    _, old_open_items = get_old_open_prs(username, since_date, headers, org)
    if index < total:
        delay()
    print(f" done ({pr_count} PRs, {commit_count} commits, "
          f"+{len(old_open_items)} old open, +{len(old_merged_items)} old merged)")

    return (pr_count, merged_items, unmerged_items, old_merged_items, old_open_items,
            merged_count, commit_items, reviews_given, prs_commented)


def _process_commits(
    merged_items: List[GitHubItem], unmerged_items: List[GitHubItem],
    old_merged_items: List[GitHubItem], old_open_items: List[GitHubItem],
    commit_items: List[GitHubItem], headers: Headers, username: str,
    since: datetime, end: datetime
) -> Tuple[List[CommitItem], int]:
    """Process and filter commits from both search and PR branches."""
    # Efficiently combine and dedupe PR items
    all_pr_items = _dedupe_pr_items(
        merged_items + unmerged_items + old_merged_items + old_open_items,
    )

    pr_branch_commits = fetch_pr_branch_commits(all_pr_items, headers, username)

    # Use set for O(1) lookup instead of list comprehension with 'in' check
    search_shas = {item.get("sha") for item in commit_items if item.get("sha")}

    # Filter PR commits more efficiently
    unique_pr_commits = []
    for commit in pr_branch_commits:
        sha = commit.get("sha")
        if sha and sha not in search_shas:
            unique_pr_commits.append(commit)

    # Combine all commits and filter by window
    all_commit_items = _filter_commits_by_window(
        commit_items + unique_pr_commits, since, end,
    )

    # Count non-merge commits efficiently
    total_commit_count = sum(
        1 for c in all_commit_items if len(c.get("parents", [])) <= 1
    )

    return all_commit_items, total_commit_count


def _compute_user_metrics(
    pr_count: int, merged_count: int, merged_items: List[GitHubItem],
    all_commit_items: List[CommitItem], total_commit_count: int,
    working_days: int, since: datetime, end: datetime,
    reviews_given: int, prs_commented: int
) -> UserRow:
    """Compute derived metrics from API data."""
    prs_per_wd = round(pr_count / working_days, 2) if working_days else 0
    merge_rate = round(merged_count / pr_count * 100, 1) if pr_count else 0.0
    avg_merge_hrs = compute_avg_merge_hours(merged_items)
    avg_coding_days, total_coding_days = compute_coding_day_stats(
        all_commit_items, since.date(), end.date(),
    )
    commits_per_cd = (
        round(total_commit_count / total_coding_days, 1)
        if total_coding_days else 0
    )
    wknd_commits, _ = compute_weekend_commits(
        all_commit_items, since.date(), end.date(),
    )
    active_repos = count_active_repos(all_commit_items)

    return {
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
        "reviews_given": reviews_given,
        "prs_commented_on": prs_commented,
    }


def _collect_user_stats(
    username: str,
    index: int,
    total: int,
    since_date: str,
    since: datetime,
    end: datetime,
    working_days: int,
    headers: Headers,
    org: str,
) -> UserRow:
    """Fetch all API data for a single user and compute derived metrics."""
    info(f"\n[{index}/{total}] {username}")

    # Collect raw API data
    (pr_count, merged_items, unmerged_items, old_merged_items, old_open_items,
     merged_count, commit_items, reviews_given, prs_commented) = _collect_api_data(
        username, since_date, headers, org, index, total
    )

    # Process commits from search and PR branches
    all_commit_items, total_commit_count = _process_commits(
        merged_items, unmerged_items, old_merged_items, old_open_items,
        commit_items, headers, username, since, end
    )

    # Compute metrics
    metrics = _compute_user_metrics(
        pr_count, merged_count, merged_items, all_commit_items, total_commit_count,
        working_days, since, end, reviews_given, prs_commented
    )

    # Add username to result
    result: UserRow = {"username": username, **metrics}

    _print_user_summary(result)
    return result


def _dedupe_pr_items(items: List[GitHubItem]) -> List[GitHubItem]:
    """Deduplicate PR items by URL using efficient dict-based approach."""
    if not items:
        return []

    # Use dict to maintain insertion order while deduplicating
    unique_items: Dict[str, GitHubItem] = {}

    for item in items:
        url = item.get("html_url") or item.get("url")
        if url and url not in unique_items:
            unique_items[url] = item

    return list(unique_items.values())


def _filter_commits_by_window(
    commit_items: List[CommitItem], since: datetime, end: datetime,
) -> List[CommitItem]:
    """Keep only commits whose author date falls within the lookback window."""
    if not commit_items:
        return []

    filtered: List[CommitItem] = []
    since_date = since.date()
    end_date = end.date()

    for item in commit_items:
        # Use more efficient nested dict access
        commit_data = item.get("commit")
        if not commit_data:
            filtered.append(item)
            continue

        author_data = commit_data.get("author")
        if not author_data:
            filtered.append(item)
            continue

        date_str = author_data.get("date")
        if not date_str:
            filtered.append(item)
            continue

        try:
            dt = parse_iso(date_str).astimezone(MYT).date()
            if since_date <= dt <= end_date:
                filtered.append(item)
        except (ValueError, TypeError):
            # Include items with invalid dates to avoid losing data
            filtered.append(item)

    return filtered


def _print_user_summary(r: UserRow) -> None:
    """Print a one-shot summary of a single user's stats."""
    def _v(val: Any, suffix: str = "") -> str:
        return f"{val}{suffix}" if val is not None else "N/A"

    info(f"  Activity:  PRs: {r['total_prs']} "
          f"({r['prs_per_working_day']}/working day, {r['merge_rate_pct']}% merged) "
          f"| Commits: {r['total_commits']} ({r['commits_per_coding_day']}/day) "
          f"| Coding days/week: {_v(r['avg_coding_days_per_week'])} "
          f"| Weekend commits: {r['weekend_commits']}")
    info(f"  Quality:   Merge time: {_v(r['avg_merge_time_hrs'], 'h')} "
          f"| Active repos: {r['active_repos']}")
    info(f"  Collab:    Reviews: {r['reviews_given']} "
          f"| PRs commented: {r['prs_commented_on']}")


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def _export_excel(
    results: List[UserRow], run_teams: Teams, profile_name: str,
) -> str:
    """Write results to a timestamped Excel file and return the filename."""
    safe_profile = re.sub(r"[^A-Za-z0-9_.-]", "_", profile_name)
    output_file = f"github_stats_{safe_profile}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb = Workbook()

    results_by_user = {r["username"]: r for r in results}

    sheet_sets: List[Tuple[str, List[UserRow]]] = []
    if len(run_teams) > 1:
        sheet_sets.append(("All", results))
    for team_name, members in run_teams.items():
        team_results = [results_by_user[u] for u in members if u in results_by_user]
        team_results.sort(key=lambda r: r["total_commits"], reverse=True)
        sheet_sets.append((team_name, team_results))

    for idx, (sheet_name, sheet_results) in enumerate(sheet_sets):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = sheet_name[:31]
        team_avg = compute_team_averages(sheet_results) if len(sheet_results) > 1 else None
        write_stats_sheet(ws, sheet_results, team_avg=team_avg)

    wb.save(output_file)

    info("\n" + "=" * 90)
    sheets_desc = ", ".join(name for name, _ in sheet_sets)
    info(f"Excel exported → {output_file}  (sheets: {sheets_desc})")
    return output_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: setup, collect stats per user, export, and display."""
    profile_name, org_file, team_file, api_file, use_legacy_token = choose_profile()
    token = get_token(profile_name, use_legacy_token)
    org = load_org(org_file)
    api_base = load_api_base(api_file)
    set_api_base(api_base)
    validate_token(token, org, api_base)
    _, teams = load_team_members(team_file)
    team_members, run_teams = choose_team(teams)
    lookback_days = get_lookback_days()

    headers: Headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Flow never includes the current day — the window ends yesterday.
    end = datetime.now(MYT) - timedelta(days=1)
    since = end - timedelta(days=lookback_days - 1)
    since_date = since.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")
    working_days = count_working_days(since.date(), end.date())

    total = len(team_members)
    scope_label = "All teams" if len(run_teams) > 1 else list(run_teams.keys())[0]
    est_min = round(
        (total * 8 * SEARCH_API_DELAY_SECONDS
         + total * 100 * 3 / PR_BRANCH_WORKERS) / 60,
        1,
    )

    info(f"\nGitHub Stats for {total} team members  ({scope_label}, profile: {profile_name})")
    info(f"Org:            {org}")
    info(f"Period:         {since_date} → {end_date}  "
          f"({lookback_days} calendar days, {working_days} working days)")
    info(f"Estimated time: ~{est_min} min")
    info("=" * 90)

    results: List[UserRow] = []
    for i, username in enumerate(team_members, 1):
        result = _collect_user_stats(
            username, i, total, since_date, since, end, working_days, headers, org,
        )
        results.append(result)

    results.sort(key=lambda r: r["total_commits"], reverse=True)

    _export_excel(results, run_teams, profile_name)

    overall_avg = compute_team_averages(results) if len(results) > 1 else None
    print_console_tables(results, team_avg=overall_avg)


if __name__ == "__main__":
    main()
