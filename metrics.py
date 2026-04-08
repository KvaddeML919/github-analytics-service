"""Pure computation functions for deriving stats from GitHub data."""

import datetime as _dt
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

MYT = timezone(timedelta(hours=8))


def parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (with trailing ``Z`` or offset) into an aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _commit_author_date(item: Dict[str, Any]) -> Optional[date]:
    """Extract the author date from a commit item, converted to MYT.

    Returns None if the date field is missing.
    """
    date_str = item.get("commit", {}).get("author", {}).get("date")
    if not date_str:
        return None
    return parse_iso(date_str).astimezone(MYT).date()


def compute_avg_merge_hours(merged_items: List[Dict[str, Any]]) -> Optional[float]:
    """Average hours from PR creation to merge, based on fetched items."""
    durations: List[float] = []
    for item in merged_items:
        created = parse_iso(item["created_at"])
        merged_at = (item.get("pull_request") or {}).get("merged_at")
        if not merged_at:
            merged_at = item.get("closed_at")
        if not merged_at:
            continue
        hours = (parse_iso(merged_at) - created).total_seconds() / 3600
        if hours >= 0:
            durations.append(hours)
    return round(sum(durations) / len(durations), 1) if durations else None


def compute_coding_day_stats(
    commit_items: List[Dict[str, Any]], start_date: date, end_date: date,
) -> Tuple[Optional[float], int]:
    """Return (avg_coding_days_per_week, total_coding_days).

    avg_coding_days_per_week follows Flow's definition: all days
    (including weekends) count, merge commits are excluded, zero-commit
    weeks are excluded, partial weeks use Flow's normalization.

    total_coding_days is the raw count of unique days with at least one
    non-merge commit (used to derive commits-per-coding-day).
    """
    coding_dates: Set[date] = set()
    for item in commit_items:
        if len(item.get("parents", [])) > 1:
            continue
        dt = _commit_author_date(item)
        if dt and start_date <= dt <= end_date:
            coding_dates.add(dt)

    if not coding_dates:
        return None, 0

    period_days_by_week: Dict[Tuple[int, int], int] = defaultdict(int)
    current = start_date
    while current <= end_date:
        period_days_by_week[current.isocalendar()[:2]] += 1
        current += timedelta(days=1)

    coding_days_by_week: Dict[Tuple[int, int], Set[date]] = defaultdict(set)
    for d in coding_dates:
        coding_days_by_week[d.isocalendar()[:2]].add(d)

    active_weeks = set(coding_days_by_week.keys())
    total_coding_days = sum(len(days) for days in coding_days_by_week.values())
    total_days = sum(period_days_by_week[wk] for wk in active_weeks)

    if total_days == 0:
        return None, 0

    avg = (total_coding_days / total_days) * min(7, total_days)
    return round(avg, 1), total_coding_days


def compute_weekend_commits(
    commit_items: List[Dict[str, Any]], start_date: date, end_date: date,
) -> Tuple[int, float]:
    """Return (total_weekend_commits, avg_commits_per_weekend) in MYT."""
    weekend_commit_count = 0
    for item in commit_items:
        dt = _commit_author_date(item)
        if dt and start_date <= dt <= end_date and dt.weekday() >= 5:
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


def count_active_repos(commit_items: List[Dict[str, Any]]) -> int:
    """Count distinct repositories the user committed to."""
    return len({
        item.get("repository", {}).get("full_name")
        for item in commit_items
        if item.get("repository", {}).get("full_name")
    })


def count_working_days(start_date: date, end_date: date) -> int:
    """Count weekdays (Mon-Fri) between two dates, inclusive."""
    days = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days
