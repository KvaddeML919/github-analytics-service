#!/usr/bin/env python3
"""Pull PR, commit, and collaboration stats for team members from a GitHub org."""

import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

GITHUB_API = "https://api.github.com"

ORG = "MoneyLion"

TEAM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team.txt")

DEFAULT_LOOKBACK_DAYS = 90

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


def validate_token(token):
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
        f"{GITHUB_API}/orgs/{ORG}/repos",
        headers={**headers, "Accept": "application/vnd.github.v3+json"},
        params={"per_page": 1},
        timeout=15,
    )
    if org_resp.status_code == 403 or org_resp.status_code == 404:
        print(f"\nWarning: Cannot access the {ORG} org.")
        print("  If the org uses SAML SSO, you need to authorize the token:")
        print("  1. Go to https://github.com/settings/tokens")
        print("  2. Click 'Configure SSO' next to your token")
        print(f"  3. Authorize it for {ORG}")
        print()
        proceed = input("Continue anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            sys.exit(1)

    return True


def load_team_members():
    if not os.path.exists(TEAM_FILE):
        print(f"Error: Team file not found: {TEAM_FILE}")
        print("Create a team.txt file with one GitHub username per line.")
        sys.exit(1)
    with open(TEAM_FILE) as f:
        members = [line.strip() for line in f if line.strip()]
    if not members:
        print("Error: team.txt is empty. Add at least one GitHub username.")
        sys.exit(1)
    return members


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _search_request(url, params, headers, accept=None, per_page=1):
    """Execute a GitHub search and return (total_count, items)."""
    req_headers = {**headers}
    if accept:
        req_headers["Accept"] = accept
    params = {**params, "per_page": per_page, "page": 1}

    for _ in range(3):
        resp = requests.get(url, params=params, headers=req_headers, timeout=30)

        if resp.status_code == 403:
            reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset_ts - int(time.time()), 5)
            print(f"    Rate limited. Waiting {wait}s ...")
            time.sleep(wait)
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
        for _ in range(3):
            resp = requests.get(url, params=params, headers=req_headers, timeout=30)
            if resp.status_code == 403:
                reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset_ts - int(time.time()), 5)
                print(f"    Rate limited. Waiting {wait}s ...")
                time.sleep(wait)
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

def get_pr_count(username, since, headers):
    return _search_count(
        "/search/issues",
        f"type:pr author:{username} org:{ORG} created:>={since}",
        headers,
    )


def get_merged_prs(username, since, headers):
    """Return (merged_count, all merged PR items) with pagination."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{ORG} is:merged created:>={since}",
        headers,
    )


def get_commits_with_items(username, since, headers):
    """Return (commit_count, all commit items) with pagination."""
    return _search_all_items(
        "/search/commits",
        f"author:{username} org:{ORG} committer-date:>={since}",
        headers,
        accept="application/vnd.github.cloak-preview+json",
    )


def get_reviews_given(username, since, headers):
    return _search_count(
        "/search/issues",
        f"type:pr reviewed-by:{username} org:{ORG} created:>={since}",
        headers,
    )



def get_prs_commented_on(username, since, headers):
    """PRs authored by others where this user left comments."""
    return _search_count(
        "/search/issues",
        f"type:pr commenter:{username} -author:{username} org:{ORG} created:>={since}",
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


def compute_avg_coding_days(commit_items):
    """Average weekday coding days per active week (weeks with 0 commits excluded)."""
    weekday_dates = set()
    for item in commit_items:
        committer = item.get("commit", {}).get("committer", {})
        date_str = committer.get("date")
        if not date_str:
            continue
        dt = _parse_iso(date_str).date()
        if dt.weekday() < 5:
            weekday_dates.add(dt)

    if not weekday_dates:
        return None

    weeks = defaultdict(set)
    for d in weekday_dates:
        weeks[d.isocalendar()[:2]].add(d)

    coding_days_per_week = [len(days) for days in weeks.values()]
    return round(sum(coding_days_per_week) / len(coding_days_per_week), 1)


def count_active_repos(commit_items):
    return len({
        item["repository"]["full_name"]
        for item in commit_items
        if "repository" in item
    })


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


def main():
    token = get_token()
    validate_token(token)
    team_members = load_team_members()
    lookback_days = get_lookback_days()

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    since_date = since.strftime("%Y-%m-%d")
    today_date = now.strftime("%Y-%m-%d")
    working_days = count_working_days(since.date(), now.date())

    total = len(team_members)
    search_calls_per_user = 5
    est_min = round(
        (total * search_calls_per_user * SEARCH_API_DELAY_SECONDS
         + total * LINE_STATS_SAMPLE_SIZE * COMMIT_API_DELAY_SECONDS) / 60,
        1,
    )

    print(f"\nGitHub Stats for {total} team members")
    print(f"Org:            {ORG}")
    print(f"Period:         {since_date} → {today_date}  "
          f"({lookback_days} calendar days, {working_days} working days)")
    print(f"Estimated time: ~{est_min} min")
    print("=" * 90)

    results = []

    for i, username in enumerate(team_members, 1):
        print(f"\n[{i}/{total}] {username}")

        pr_count = get_pr_count(username, since_date, headers)
        _delay()

        merged_count, merged_items = get_merged_prs(username, since_date, headers)
        _delay()

        commit_count, commit_items = get_commits_with_items(username, since_date, headers)
        _delay()

        reviews_given = get_reviews_given(username, since_date, headers)
        _delay()

        prs_commented = get_prs_commented_on(username, since_date, headers)
        if i < total:
            _delay()

        # Derived metrics
        prs_per_wd = round(pr_count / working_days, 2) if working_days else 0
        commits_per_wd = round(commit_count / working_days, 2) if working_days else 0
        merge_rate = round(merged_count / pr_count * 100, 1) if pr_count else 0.0
        avg_merge_hrs = compute_avg_merge_hours(merged_items)
        avg_coding_days = compute_avg_coding_days(commit_items)
        active_repos = count_active_repos(commit_items)

        additions = deletions = sampled = 0
        if commit_items:
            additions, deletions, sampled = fetch_line_stats_sample(
                commit_items, headers,
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
            "total_commits": commit_count,
            "commits_per_working_day": commits_per_wd,
            "avg_coding_days_per_week": avg_coding_days,
            "active_repos": active_repos,
            "reviews_given": reviews_given,
            "prs_commented_on": prs_commented,
            "avg_additions_per_commit": avg_add,
            "avg_deletions_per_commit": avg_del,
        }
        results.append(result)

        merge_str = f"{avg_merge_hrs}h" if avg_merge_hrs is not None else "N/A"
        coding_str = f"{avg_coding_days}" if avg_coding_days is not None else "N/A"
        print(f"  Activity:  PRs: {pr_count} ({prs_per_wd}/wd, {merge_rate}% merged) "
              f"| Commits: {commit_count} ({commits_per_wd}/wd) "
              f"| Coding days/wk: {coding_str}")
        print(f"  Quality:   Avg merge time: {merge_str} "
              f"| Active repos: {active_repos} "
              f"| Avg lines/commit: +{avg_add}/-{avg_del}")
        print(f"  Collab:    Reviews given: {reviews_given} "
              f"| PRs commented: {prs_commented}")

    results.sort(key=lambda r: r["total_commits"], reverse=True)

    # ── CSV export ──────────────────────────────────────────────────────────
    output_file = f"github_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    fieldnames = list(results[0].keys()) if results else []

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = {**r}
            if row["avg_merge_time_hrs"] is None:
                row["avg_merge_time_hrs"] = ""
            if row["avg_coding_days_per_week"] is None:
                row["avg_coding_days_per_week"] = ""
            writer.writerow(row)

    print("\n" + "=" * 90)
    print(f"CSV exported → {output_file}")

    # ── Table 1: Activity ───────────────────────────────────────────────────
    print(f"\n{'─' * 80}")
    print("ACTIVITY")
    hdr1 = (f"{'Username':<20} {'PRs':>5} {'PRs/wd':>7} {'Merged%':>8} "
            f"{'Commits':>8} {'Cmts/wd':>8} {'CdDays/wk':>10}")
    print(hdr1)
    print("─" * len(hdr1))
    for r in results:
        cd = r["avg_coding_days_per_week"]
        cd_str = f"{cd}" if cd is not None else "N/A"
        print(f"{r['username']:<20} "
              f"{r['total_prs']:>5} "
              f"{r['prs_per_working_day']:>7} "
              f"{r['merge_rate_pct']:>7.1f}% "
              f"{r['total_commits']:>8} "
              f"{r['commits_per_working_day']:>8} "
              f"{cd_str:>10}")

    # ── Table 2: Collaboration & Quality ────────────────────────────────────
    print(f"\n{'─' * 80}")
    print("COLLABORATION & QUALITY")
    hdr2 = (f"{'Username':<20} {'Reviews':>8} {'Commented':>10} "
            f"{'Merge(h)':>9} {'Repos':>6} {'+Lines/c':>9} {'-Lines/c':>9}")
    print(hdr2)
    print("─" * len(hdr2))
    for r in results:
        m = r["avg_merge_time_hrs"]
        merge_str = f"{m}" if m is not None else "N/A"
        add_str = f"+{r['avg_additions_per_commit']}"
        del_str = f"-{r['avg_deletions_per_commit']}"
        print(f"{r['username']:<20} "
              f"{r['reviews_given']:>8} "
              f"{r['prs_commented_on']:>10} "
              f"{merge_str:>9} "
              f"{r['active_repos']:>6} "
              f"{add_str:>9} "
              f"{del_str:>9}")


if __name__ == "__main__":
    main()
