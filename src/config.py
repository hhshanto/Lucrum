"""Application configuration.

Read once at startup. Nothing here carries a business default: a value the
business has not decided is a blocker to be reported, not a constant to be
invented.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationMissing(RuntimeError):
    """A required configuration value is absent.

    Raised rather than defaulted. An unconfigured system must refuse, not guess
    -- substituting a plausible value is the single most damaging thing an
    implementation can do here.
    """

    def __init__(self, key: str, why: str) -> None:
        super().__init__(f"{key} is not configured. {why}")
        self.key = key


def _required(key: str, why: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise ConfigurationMissing(key, why)
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_env: str

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def load_settings() -> Settings:
    return Settings(
        database_url=_required(
            "DATABASE_URL",
            "Start the local database with 'docker compose up -d' and copy "
            ".env.example to .env.",
        ),
        # No default. Every other value here refuses rather than guesses, and
        # this one's unsafe direction is silently reporting a production
        # host as development.
        app_env=_required(
            "APP_ENV", "Set APP_ENV to 'development' or 'production'."
        ),
    )
