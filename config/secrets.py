"""
config/secrets.py
==================
Centralized secrets loader and validator.

On startup, validates all required secrets are present.
Refuses to start if critical secrets are missing.
Logs which secrets are configured (without revealing values).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.config.secrets")

_DATA_DIR = Path(__file__).parent.parent / "data"


# Required secrets and their criticality
REQUIRED_SECRETS = {
    # Critical — system won't start without these
    "NZT48_API_KEY": {"critical": True, "description": "Dashboard API authentication key"},
    # Important but not blocking
    "TELEGRAM_BOT_TOKEN": {"critical": False, "description": "Telegram bot token for delivery"},
    "TELEGRAM_CHAT_ID": {"critical": False, "description": "Telegram chat ID for delivery"},
    "GEMINI_API_KEY": {"critical": False, "description": "Google Gemini AI API key"},
}

# Optional secrets
OPTIONAL_SECRETS = {
    "TWELVEDATA_API_KEY": "TwelveData backup feed",
    "ALPHA_VANTAGE_KEY": "Alpha Vantage backup feed",
    "FMP_API_KEY": "Financial Modeling Prep backup feed",
    "AWS_S3_BUCKET": "S3 bucket for backups",
    "AWS_ACCESS_KEY_ID": "AWS access key",
    "AWS_SECRET_ACCESS_KEY": "AWS secret key",
}


def validate_secrets(fail_on_critical: bool = True) -> dict[str, bool]:
    """Validate all required secrets are present.

    Returns dict of secret_name -> is_present.
    Raises RuntimeError if critical secrets are missing and fail_on_critical=True.
    """
    results: dict[str, bool] = {}
    missing_critical: list[str] = []
    missing_optional: list[str] = []

    for name, info in REQUIRED_SECRETS.items():
        value = os.environ.get(name, "")
        present = bool(value and value.strip())
        results[name] = present

        if present:
            # Log presence without revealing value
            masked = value[:3] + "***" if len(value) > 3 else "***"
            logger.info("SECRET [%s]: configured (%s) — %s",
                       "CRITICAL" if info["critical"] else "IMPORTANT",
                       masked, info["description"])
        else:
            if info["critical"]:
                missing_critical.append(name)
                logger.error("SECRET [CRITICAL]: %s NOT SET — %s", name, info["description"])
            else:
                missing_optional.append(name)
                logger.warning("SECRET [IMPORTANT]: %s NOT SET — %s", name, info["description"])

    # Check optional secrets
    for name, description in OPTIONAL_SECRETS.items():
        value = os.environ.get(name, "")
        present = bool(value and value.strip())
        results[name] = present
        if present:
            logger.info("SECRET [OPTIONAL]: %s configured — %s", name, description)

    # Summary
    total = len(REQUIRED_SECRETS) + len(OPTIONAL_SECRETS)
    configured = sum(1 for v in results.values() if v)
    logger.info("SECRETS: %d/%d configured, %d critical missing, %d optional missing",
                configured, total, len(missing_critical), len(missing_optional))

    if missing_critical and fail_on_critical:
        raise RuntimeError(
            f"FATAL: Missing critical secrets: {', '.join(missing_critical)}. "
            f"Set these environment variables before starting NZT-48."
        )

    return results


def get_secret(name: str, default: str = "") -> str:
    """Get a secret value from environment."""
    return os.environ.get(name, default)


def check_rotation_age() -> None:
    """Check if secrets need rotation (>90 days since last rotation)."""
    tracker_file = _DATA_DIR / "secrets_last_rotated.json"

    if not tracker_file.exists():
        # Create initial tracker
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tracker = {
            "last_rotated": datetime.now(timezone.utc).isoformat(),
            "note": "Update this file when secrets are rotated",
        }
        tracker_file.write_text(json.dumps(tracker, indent=2))
        logger.info("SECRETS: rotation tracker initialized")
        return

    try:
        data = json.loads(tracker_file.read_text())
        last_rotated = datetime.fromisoformat(data["last_rotated"].replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - last_rotated).days

        if age_days > 90:
            logger.warning(
                "SECRETS: last rotation was %d days ago (>90 days) — consider rotating secrets",
                age_days,
            )
        else:
            logger.info("SECRETS: last rotation %d days ago — OK", age_days)
    except Exception as e:
        logger.warning("SECRETS: rotation age check failed: %s", e)
