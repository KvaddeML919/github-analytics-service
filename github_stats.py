#!/usr/bin/env python3
"""Pull PR, commit, and collaboration stats for team members from a GitHub org."""

import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import requests
from openpyxl import Workbook

from github_api import (
    GITHUB_API,
    SEARCH_API_DELAY_SECONDS,
    PR_BRANCH_WORKERS,
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
    fetch_pr_response_times,
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEAM_FILE = os.path.join(SCRIPT_DIR, "team.txt")
ORG_FILE = os.path.join(SCRIPT_DIR, "org.txt")

DEFAULT_LOOKBACK_DAYS = 90

Headers = Dict[str, str]
Teams = OrderedDict  # OrderedDict[str, List[str]]
Row = Dict[str, Any]


# ---------------------------------------------------------------------------
# Setup helpers (token, org, team)
# ---------------------------------------------------------------------------

def get_token() -> str:
    """Read token from GITHUB_TOKEN env var, or prompt interactively."""
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


def validate_token(token: str, org: str) -> None:
    """Check token validity, required scopes, and org access. Exits on failure."""
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
    if org_resp.status_code in (403, 404):
        print(f"\nWarning: Cannot access the {org} org.")
        print("  If the org uses SAML SSO, you need to authorize the token:")
        print("  1. Go to https://github.com/settings/tokens")
        print("  2. Click 'Configure SSO' next to your token")
        print(f"  3. Authorize it for {org}")
        print()
        proceed = input("Continue anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            sys.exit(1)


def load_org() -> str:
    """Load org name from org.txt, or prompt and save it."""
    if os.path.exists(ORG_FILE):
        with open(ORG_FILE) as fh:
            org = fh.read().strip()
            if org:
                print(f"Organization: {org}")
                return org
    org = input("Enter the GitHub organization name: ").strip()
    if not org:
        print("Error: No organization name provided.")
        sys.exit(1)
    with open(ORG_FILE, "w") as fh:
        fh.write(org + "\n")
    print(f"Saved org '{org}' to {ORG_FILE}")
    return org


def _create_team_interactive() -> Tuple[List[str], Teams]:
    """Walk the user through creating teams and members interactively."""
    print("No team file found. Let's set up your teams now.\n")
    print("You'll enter team names first, then GitHub usernames for each team.")
    print("Press Enter on an empty line to finish each step.\n")

    teams: Teams = OrderedDict()
    total_members = 0

    while True:
        team_name = input("  Team name (empty to finish adding teams): ").strip()
        if not team_name:
            break

        teams[team_name] = []
        print(f"    Adding members to {team_name}...")

        while True:
            username = input("      GitHub username (empty to finish this team): ").strip()
            if not username:
                break
            teams[team_name].append(username)
            total_members += 1

        print(f"    Added {len(teams[team_name])} member(s) to {team_name}.\n")

    if total_members == 0:
        print("Error: No team members added.")
        sys.exit(1)

    with open(TEAM_FILE, "w") as fh:
        for tname, members in teams.items():
            fh.write(f"[{tname}]\n")
            for m in members:
                fh.write(f"{m}\n")
            fh.write("\n")

    print(f"Saved {total_members} member(s) across {len(teams)} team(s) to {TEAM_FILE}\n")

    all_members = [u for members in teams.values() for u in members]
    return all_members, teams


def load_team_members() -> Tuple[List[str], Teams]:
    """Parse team.txt or create it interactively if missing.

    Grouped format uses [TeamName] headers. Returns (all_members, teams_dict)
    where teams_dict is an OrderedDict of team_name -> [usernames].
    """
    if not os.path.exists(TEAM_FILE):
        return _create_team_interactive()

    teams: Teams = OrderedDict()
    current_team = None
    header_re = re.compile(r"^\[(.+)\]\s*$")

    with open(TEAM_FILE) as fh:
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
        print("Error: team.txt is empty. Add at least one GitHub username.")
        sys.exit(1)

    return all_members, teams


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def get_lookback_days() -> int:
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


def choose_team(teams: Teams) -> Tuple[List[str], Teams]:
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
        raw = input("\nRun report for which team? [0 = All]: ").strip()
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


# ---------------------------------------------------------------------------
# Per-user data collection
# ---------------------------------------------------------------------------

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
) -> Row:
    """Fetch all API data for a single user and compute derived metrics."""
    print(f"\n[{index}/{total}] {username}")

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

    all_pr_items = _dedupe_pr_items(
        merged_items + unmerged_items + old_merged_items + old_open_items,
    )

    pr_branch_commits = fetch_pr_branch_commits(all_pr_items, headers, username)
    search_shas = {item.get("sha") for item in commit_items}
    unique_pr_commits = [
        c for c in pr_branch_commits if c.get("sha") not in search_shas
    ]

    all_commit_items = _filter_commits_by_window(
        commit_items + unique_pr_commits, since, end,
    )
    non_merge_items = [
        c for c in all_commit_items if len(c.get("parents", [])) <= 1
    ]
    total_commit_count = len(non_merge_items)

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

    avg_reaction_hrs, avg_first_comment_hrs = fetch_pr_response_times(
        all_pr_items, headers, username,
    )

    result: Row = {
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
    }

    _print_user_summary(result)
    return result


def _dedupe_pr_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate PR items by URL."""
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for item in items:
        url = item.get("html_url") or item.get("url")
        if url and url not in seen:
            seen.add(url)
            deduped.append(item)
    return deduped


def _filter_commits_by_window(
    commit_items: List[Dict[str, Any]], since: datetime, end: datetime,
) -> List[Dict[str, Any]]:
    """Keep only commits whose author date falls within the lookback window."""
    filtered: List[Dict[str, Any]] = []
    for item in commit_items:
        date_str = item.get("commit", {}).get("author", {}).get("date")
        if not date_str:
            filtered.append(item)
            continue
        dt = parse_iso(date_str).astimezone(MYT).date()
        if since.date() <= dt <= end.date():
            filtered.append(item)
    return filtered


def _print_user_summary(r: Row) -> None:
    """Print a one-shot summary of a single user's stats."""
    def _v(val: Any, suffix: str = "") -> str:
        return f"{val}{suffix}" if val is not None else "N/A"

    print(f"  Activity:  PRs: {r['total_prs']} "
          f"({r['prs_per_working_day']}/working day, {r['merge_rate_pct']}% merged) "
          f"| Commits: {r['total_commits']} ({r['commits_per_coding_day']}/day) "
          f"| Coding days/week: {_v(r['avg_coding_days_per_week'])} "
          f"| Weekend commits: {r['weekend_commits']}")
    print(f"  Quality:   Merge time: {_v(r['avg_merge_time_hrs'], 'h')} "
          f"| Active repos: {r['active_repos']}")
    print(f"  Collab:    Reviews: {r['reviews_given']} "
          f"| PRs commented: {r['prs_commented_on']} "
          f"| Reaction time: {_v(r['avg_reaction_time_hrs'], 'h')} "
          f"| 1st comment: {_v(r['avg_first_comment_hrs'], 'h')}")


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def _export_excel(
    results: List[Row], run_teams: Teams,
) -> str:
    """Write results to a timestamped Excel file and return the filename."""
    output_file = f"github_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb = Workbook()

    results_by_user = {r["username"]: r for r in results}

    sheet_sets: List[Tuple[str, List[Row]]] = []
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

    print("\n" + "=" * 90)
    sheets_desc = ", ".join(name for name, _ in sheet_sets)
    print(f"Excel exported → {output_file}  (sheets: {sheets_desc})")
    return output_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: setup, collect stats per user, export, and display."""
    token = get_token()
    org = load_org()
    validate_token(token, org)
    _, teams = load_team_members()
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

    print(f"\nGitHub Stats for {total} team members  ({scope_label})")
    print(f"Org:            {org}")
    print(f"Period:         {since_date} → {end_date}  "
          f"({lookback_days} calendar days, {working_days} working days)")
    print(f"Estimated time: ~{est_min} min")
    print("=" * 90)

    results: List[Row] = []
    for i, username in enumerate(team_members, 1):
        result = _collect_user_stats(
            username, i, total, since_date, since, end, working_days, headers, org,
        )
        results.append(result)

    results.sort(key=lambda r: r["total_commits"], reverse=True)

    _export_excel(results, run_teams)

    overall_avg = compute_team_averages(results) if len(results) > 1 else None
    print_console_tables(results, team_avg=overall_avg)


if __name__ == "__main__":
    main()
