"""GitHub API helpers, search functions, and per-user query functions."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

GITHUB_API = "https://api.github.com"

SEARCH_API_DELAY_SECONDS = 2.5

MAX_RATE_LIMIT_WAIT = 120

PR_BRANCH_WORKERS = 8

Headers = Dict[str, str]
SearchResult = Tuple[int, List[Dict[str, Any]]]


def _handle_rate_limit(resp: requests.Response, attempt: int, max_attempts: int) -> int:
    """Handle 403 rate-limit responses. Sleeps and returns seconds waited."""
    reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
    wait = max(reset_ts - int(time.time()), 5)
    if wait > MAX_RATE_LIMIT_WAIT:
        wait = MAX_RATE_LIMIT_WAIT
    print(f"    Rate limited (attempt {attempt}/{max_attempts}). Waiting {wait}s ...")
    time.sleep(wait)
    return wait


def _search_request(
    url: str,
    params: Dict[str, Any],
    headers: Headers,
    accept: Optional[str] = None,
    per_page: int = 1,
) -> SearchResult:
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


def _search_count(
    endpoint: str, query: str, headers: Headers, accept: Optional[str] = None,
) -> int:
    """Run a search and return only the total_count."""
    count, _ = _search_request(
        f"{GITHUB_API}{endpoint}", {"q": query}, headers, accept,
    )
    return count


def _search_all_items(
    endpoint: str, query: str, headers: Headers, accept: Optional[str] = None,
) -> SearchResult:
    """Paginate through all search results (GitHub caps at 1000)."""
    url = f"{GITHUB_API}{endpoint}"
    all_items: List[Dict[str, Any]] = []
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
        delay()

    return total_count, all_items


def delay() -> None:
    """Sleep between search API calls to respect rate limits."""
    time.sleep(SEARCH_API_DELAY_SECONDS)


# ---------------------------------------------------------------------------
# Per-user query functions
# ---------------------------------------------------------------------------

def get_pr_count(username: str, since: str, headers: Headers, org: str) -> int:
    """Count PRs opened by the user in the lookback window."""
    return _search_count(
        "/search/issues",
        f"type:pr author:{username} org:{org} created:>={since}",
        headers,
    )


def get_merged_prs(username: str, since: str, headers: Headers, org: str) -> SearchResult:
    """Return (merged_count, all merged PR items) with pagination."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{org} is:merged created:>={since}",
        headers,
    )


def get_unmerged_prs(username: str, since: str, headers: Headers, org: str) -> SearchResult:
    """Return (count, items) for all unmerged PRs (open, draft, and closed-without-merging)."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{org} is:unmerged created:>={since}",
        headers,
    )


def get_old_merged_prs(username: str, since: str, headers: Headers, org: str) -> SearchResult:
    """PRs created before the window but merged during it."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{org} is:merged created:<{since} merged:>={since}",
        headers,
    )


def get_old_open_prs(username: str, since: str, headers: Headers, org: str) -> SearchResult:
    """PRs created before the window that are still open (may have recent commits)."""
    return _search_all_items(
        "/search/issues",
        f"type:pr author:{username} org:{org} is:open created:<{since}",
        headers,
    )


def get_commits_with_items(username: str, since: str, headers: Headers, org: str) -> SearchResult:
    """Return (commit_count, all commit items) with pagination."""
    return _search_all_items(
        "/search/commits",
        f"author:{username} org:{org} author-date:>={since}",
        headers,
        accept="application/vnd.github.cloak-preview+json",
    )


def get_reviews_given(username: str, since: str, headers: Headers, org: str) -> int:
    """Count PRs where the user submitted a review in the lookback window."""
    return _search_count(
        "/search/issues",
        f"type:pr reviewed-by:{username} org:{org} created:>={since}",
        headers,
    )


def get_prs_commented_on(username: str, since: str, headers: Headers, org: str) -> int:
    """Count other authors' PRs where this user left comments."""
    return _search_count(
        "/search/issues",
        f"type:pr commenter:{username} -author:{username} org:{org} created:>={since}",
        headers,
    )


# ---------------------------------------------------------------------------
# PR-level fetchers (use thread pools)
# ---------------------------------------------------------------------------

def fetch_pr_branch_commits(
    pr_items: List[Dict[str, Any]], headers: Headers, username: str,
) -> List[Dict[str, Any]]:
    """Fetch commit objects from PR branches authored by ``username``.

    GitHub's commit search only indexes default-branch commits. For
    squash-merged PRs the individual branch commits are lost, and
    open/draft PR branches are never on the default branch. This
    retrieves them via the PR commits endpoint.

    Returns a list of commit dicts (deduplicated by SHA).
    """
    total = len(pr_items)
    if not total:
        return []
    print(f"  Fetching PR branch commits ({total} PRs) ...")

    uname = username.lower()

    def _fetch_commits_for_pr(item: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        except requests.exceptions.RequestException as exc:
            print(f"    Warning: failed to fetch commits for PR: {exc}")
        return []

    commits: List[Dict[str, Any]] = []
    seen_shas: set = set()
    with ThreadPoolExecutor(max_workers=PR_BRANCH_WORKERS) as pool:
        for batch in pool.map(_fetch_commits_for_pr, pr_items):
            for c in batch:
                sha = c.get("sha")
                if sha and sha not in seen_shas:
                    seen_shas.add(sha)
                    commits.append(c)
    return commits


def fetch_pr_response_times(
    pr_items: List[Dict[str, Any]], headers: Headers, username: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Compute reaction time and time-to-first-comment for a user's PRs.

    For each PR authored by ``username``, fetches reviews and issue comments
    to find the earliest response from someone other than the author.

    Returns (avg_reaction_hrs, avg_first_comment_hrs) as floats rounded to 1
    decimal, or None when no data is available.
    """
    if not pr_items:
        return None, None

    uname = username.lower()
    print(f"  Fetching PR response times ({len(pr_items)} PRs) ...")

    def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
        if not ts:
            return None
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def _fetch_response_for_pr(
        item: Dict[str, Any],
    ) -> Tuple[Optional[float], Optional[float]]:
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
        except requests.exceptions.RequestException as exc:
            print(f"    Warning: failed to fetch reviews: {exc}")

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
        except requests.exceptions.RequestException as exc:
            print(f"    Warning: failed to fetch comments: {exc}")

        reaction_dt = None
        for dt in (first_review_dt, first_comment_dt):
            if dt and (reaction_dt is None or dt < reaction_dt):
                reaction_dt = dt

        reaction_hrs = (reaction_dt - created).total_seconds() / 3600 if reaction_dt else None
        comment_hrs = (first_comment_dt - created).total_seconds() / 3600 if first_comment_dt else None
        return reaction_hrs, comment_hrs

    reaction_hours: List[float] = []
    comment_hours: List[float] = []
    with ThreadPoolExecutor(max_workers=PR_BRANCH_WORKERS) as pool:
        for r_hrs, c_hrs in pool.map(_fetch_response_for_pr, pr_items):
            if r_hrs is not None:
                reaction_hours.append(r_hrs)
            if c_hrs is not None:
                comment_hours.append(c_hrs)

    avg_reaction = round(sum(reaction_hours) / len(reaction_hours), 1) if reaction_hours else None
    avg_comment = round(sum(comment_hours) / len(comment_hours), 1) if comment_hours else None
    return avg_reaction, avg_comment
