"""Configuration management for GitHub Analytics Service."""

import os
from dataclasses import dataclass
from datetime import timezone, timedelta
from typing import Optional


@dataclass
class Config:
    """Centralized configuration for the GitHub Analytics Service."""

    # GitHub API Configuration
    github_api_base: str = "https://api.github.com"
    github_token: Optional[str] = None

    # Rate Limiting & Performance
    search_api_delay_seconds: float = 2.5
    max_rate_limit_wait_seconds: int = 120
    pr_branch_workers: int = 8
    request_timeout_seconds: int = 30
    max_retries: int = 3

    # Default Settings
    default_lookback_days: int = 90

    # GitHub API Limits
    github_search_results_limit: int = 1000  # GitHub's hard limit
    commits_per_page: int = 250  # PR commits endpoint limit
    search_results_per_page: int = 100

    # File Paths
    team_file_name: str = "team.txt"
    org_file_name: str = "org.txt"
    api_file_name: str = "api.txt"
    profiles_dir_name: str = "profiles"

    # Timezone
    local_timezone: timezone = timezone(timedelta(hours=8))  # MYT

    def __post_init__(self):
        """Initialize configuration from environment variables."""
        # Override with environment variables if available
        self.github_token = os.environ.get("GITHUB_TOKEN", self.github_token)

        # Allow environment variable overrides
        self.search_api_delay_seconds = float(
            os.environ.get("SEARCH_API_DELAY_SECONDS", self.search_api_delay_seconds)
        )
        self.max_rate_limit_wait_seconds = int(
            os.environ.get("MAX_RATE_LIMIT_WAIT_SECONDS", self.max_rate_limit_wait_seconds)
        )
        self.pr_branch_workers = int(
            os.environ.get("PR_BRANCH_WORKERS", self.pr_branch_workers)
        )
        self.request_timeout_seconds = int(
            os.environ.get("REQUEST_TIMEOUT_SECONDS", self.request_timeout_seconds)
        )
        self.default_lookback_days = int(
            os.environ.get("DEFAULT_LOOKBACK_DAYS", self.default_lookback_days)
        )


# Global configuration instance
config = Config()

# Constants for backward compatibility
GITHUB_API = config.github_api_base
SEARCH_API_DELAY_SECONDS = config.search_api_delay_seconds
MAX_RATE_LIMIT_WAIT = config.max_rate_limit_wait_seconds
PR_BRANCH_WORKERS = config.pr_branch_workers
DEFAULT_LOOKBACK_DAYS = config.default_lookback_days
MYT = config.local_timezone