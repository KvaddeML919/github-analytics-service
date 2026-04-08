#!/usr/bin/env python3
"""Pull PR, commit, and collaboration stats for team members from a GitHub org."""

import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timedelta

import requests
from openpyxl import Workbook

from github_api import (
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
    GITHUB_API,
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
        print("No team file found. Let's set up your teams now.\n")
        print("You'll enter team names first, then GitHub usernames for each team.")
        print("Press Enter on an empty line to finish each step.\n")

        teams = OrderedDict()
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

        with open(TEAM_FILE, "w") as f:
            for tname, members in teams.items():
                f.write(f"[{tname}]\n")
                for m in members:
                    f.write(f"{m}\n")
                f.write("\n")

        print(f"Saved {total_members} member(s) across {len(teams)} team(s) to {TEAM_FILE}\n")

        all_members = [u for members in teams.values() for u in members]
        return all_members, teams

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
# CLI helpers
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    search_calls_per_user = 8
    avg_prs_per_user = 100
    pr_calls_per_pr = 3
    pr_fetch_seconds = avg_prs_per_user * pr_calls_per_pr / PR_BRANCH_WORKERS
    est_min = round(
        (total * search_calls_per_user * SEARCH_API_DELAY_SECONDS
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
        if i < total:
            delay()
        print(f" done ({pr_count} PRs, {commit_count} commits, "
              f"+{len(old_open_items)} old open, +{len(old_merged_items)} old merged)")

        # Combine in-window PRs with old PRs (deduplicate by URL).
        # Using author date for coding days means old-authored commits from
        # long-ago merged PRs correctly fall outside the window, while
        # recently-authored commits on any branch are captured.
        seen_pr_urls = set()
        all_pr_items = []
        for item in merged_items + unmerged_items + old_merged_items + old_open_items:
            url = item.get("html_url") or item.get("url")
            if url and url not in seen_pr_urls:
                seen_pr_urls.add(url)
                all_pr_items.append(item)

        pr_branch_commits = fetch_pr_branch_commits(
            all_pr_items, headers, username,
        )
        search_shas = {item.get("sha") for item in commit_items}
        unique_pr_commits = [
            c for c in pr_branch_commits if c.get("sha") not in search_shas
        ]
        all_commit_items_raw = commit_items + unique_pr_commits

        # Filter to commits authored within the lookback window
        all_commit_items = []
        for item in all_commit_items_raw:
            author = item.get("commit", {}).get("author", {})
            date_str = author.get("date")
            if not date_str:
                all_commit_items.append(item)
                continue
            dt = parse_iso(date_str).astimezone(MYT).date()
            if since.date() <= dt <= now.date():
                all_commit_items.append(item)
        total_commit_count = len(all_commit_items)

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

        avg_reaction_hrs, avg_first_comment_hrs = fetch_pr_response_times(
            all_pr_items, headers, username,
        )

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
              f"| Active repos: {active_repos}")
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
        team_avg = compute_team_averages(sheet_results) if len(sheet_results) > 1 else None
        write_stats_sheet(ws, sheet_results, team_avg=team_avg)

    wb.save(output_file)

    print("\n" + "=" * 90)
    sheets_desc = ", ".join(name for name, _ in sheet_sets)
    print(f"Excel exported → {output_file}  (sheets: {sheets_desc})")

    # ── Console tables ──────────────────────────────────────────────────────
    overall_avg = compute_team_averages(results) if len(results) > 1 else None
    print_console_tables(results, team_avg=overall_avg)


if __name__ == "__main__":
    main()
