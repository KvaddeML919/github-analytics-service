import os
import unittest
from datetime import date
from unittest.mock import patch

import requests

import github_api
import github_stats
from metrics import compute_weekend_commits
from output import print_console_tables


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class SearchRegressionTests(unittest.TestCase):
    def test_search_results_stop_at_configured_limit_with_warning(self):
        pages = []

        def fake_get(_url, params, **_kwargs):
            pages.append(params["page"])
            first_id = (params["page"] - 1) * 2
            items = [{"id": first_id + offset} for offset in range(2)]
            return FakeResponse(payload={"total_count": 5, "items": items})

        with (
            patch.object(github_api.config, "search_results_per_page", 2),
            patch.object(github_api.config, "github_search_results_limit", 4),
            patch.object(github_api.requests, "get", side_effect=fake_get),
            patch.object(github_api, "delay"),
            patch.object(github_api, "warning") as warning,
        ):
            total, items = github_api._search_all_items("/search/issues", "test", {})

        self.assertEqual(total, 5)
        self.assertEqual(len(items), 4)
        self.assertEqual(pages, [1, 2])
        warning.assert_called_once()

    def test_search_validation_error_is_not_reported_as_zero(self):
        with patch.object(
            github_api.requests, "get", return_value=FakeResponse(status_code=422)
        ):
            with self.assertRaises(requests.HTTPError):
                github_api._search_request("https://api.github.com/search/issues", {}, {})

    def test_paginated_search_validation_error_is_not_reported_as_empty(self):
        with patch.object(
            github_api.requests, "get", return_value=FakeResponse(status_code=422)
        ):
            with self.assertRaises(requests.HTTPError):
                github_api._search_all_items("/search/issues", "test", {})

    def test_exhausted_rate_limit_is_not_reported_as_zero(self):
        with (
            patch.object(github_api.config, "max_retries", 1),
            patch.object(github_api, "_handle_rate_limit"),
            patch.object(
                github_api.requests, "get", return_value=FakeResponse(status_code=403)
            ),
        ):
            with self.assertRaises(requests.HTTPError):
                github_api._search_request("https://api.github.com/search/issues", {}, {})

    def test_pr_branch_commits_are_fully_paginated(self):
        pages = []

        def fake_get(_url, params, **_kwargs):
            page = params["page"]
            pages.append(page)
            start = (page - 1) * 2
            commits = [
                {"sha": f"sha-{index}", "author": {"login": "alice"}}
                for index in range(start, min(start + 2, 3))
            ]
            return FakeResponse(payload=commits)

        pr_items = [{
            "pull_request": {
                "url": "https://api.github.com/repos/acme/repo/pulls/1",
            },
        }]
        with (
            patch.object(github_api.config, "commits_per_page", 2),
            patch.object(github_api.config, "pr_branch_workers", 1),
            patch.object(github_api.requests, "get", side_effect=fake_get),
            patch.object(github_api, "info"),
        ):
            commits = github_api.fetch_pr_branch_commits(pr_items, {}, "alice")

        self.assertEqual(pages, [1, 2])
        self.assertEqual([commit["sha"] for commit in commits], ["sha-0", "sha-1", "sha-2"])


class TokenPromptTests(unittest.TestCase):
    def test_interactive_token_prompt_does_not_echo_secret(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.input", side_effect=AssertionError("input must not be used")),
            patch("github_stats.getpass.getpass", return_value="test-token"),
        ):
            token = github_stats.get_token("default", use_legacy_token=False)

        self.assertEqual(token, "test-token")


class WeekendCommitMetricTests(unittest.TestCase):
    def test_weekend_count_excludes_merge_commits(self):
        commit_items = [
            {
                "commit": {"author": {"date": "2026-10-03T09:00:00+08:00"}},
                "parents": [{"sha": "parent-1"}],
            },
            {
                "commit": {"author": {"date": "2026-10-04T09:00:00+08:00"}},
                "parents": [{"sha": "parent-1"}, {"sha": "parent-2"}],
            },
            {
                "commit": {"author": {"date": "2026-10-02T09:00:00+08:00"}},
                "parents": [{"sha": "parent-1"}],
            },
        ]

        count, average = compute_weekend_commits(
            commit_items, date(2026, 10, 3), date(2026, 10, 4),
        )

        self.assertEqual(count, 1)
        self.assertEqual(average, 1.0)

    def test_console_header_uses_full_weekend_commits_label(self):
        with patch("output.info") as info:
            print_console_tables([])

        output_lines = [call.args[0] for call in info.call_args_list]
        self.assertTrue(any("Weekend Commits" in line for line in output_lines))


if __name__ == "__main__":
    unittest.main()