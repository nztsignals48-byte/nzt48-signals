"""
NZT-48 Trading System -- ArtifactLoader
=========================================
Single artifact consumer for all delivery surfaces (PDF, Telegram,
War Room, Dashboard). Loads session artifacts from disk, assembles
them into an ArtifactBundle, and provides query methods.

Artifact directory layout:
    artifacts/{date}/{session}/
        plays.json              -- List of PlayCard dicts
        peers_intel.json        -- Peer/comparative plays
        full_scan.json          -- All scanned candidates
        regime.json             -- RegimeSnapshot dict
        data_health.json        -- DataHealthReport dict
        system_state.json       -- SystemStateReport dict
        drought.json            -- DroughtReport dict (if no plays)
        opportunity.json        -- List of OpportunityCandidate dicts
        manifest.json           -- TruthManifest dict

Cache: loaded once per session, cleared on new session.

Usage:
    from core.artifact_loader import ArtifactLoader
    loader = ArtifactLoader(base_dir="artifacts")
    bundle = loader.load_session("2026-02-26", "pre_lse")
    plays = loader.get_plays()
    manifest = loader.get_manifest()
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from core.schemas import ArtifactBundle, TruthManifest

logger = logging.getLogger("nzt48.core.artifact_loader")

# Default base directory for artifacts (relative to project root)
_DEFAULT_BASE_DIR = "artifacts"


class ArtifactLoader:
    """Loads and caches artifact bundles from disk.

    All delivery surfaces consume artifacts through this loader to
    guarantee they are reading the same data. The loader validates
    the truth manifest hash on load.
    """

    def __init__(self, base_dir: str = _DEFAULT_BASE_DIR) -> None:
        self._base_dir = Path(base_dir)
        self._cached_bundle: Optional[ArtifactBundle] = None
        self._cached_date: str = ""
        self._cached_session: str = ""

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def load_session(self, date: str, session: str) -> ArtifactBundle:
        """Load all artifacts for a given date and session.

        If the same date+session is already cached, returns the cached
        bundle without re-reading disk.

        Args:
            date:    Date string, e.g. "2026-02-26".
            session: Session name, e.g. "pre_lse", "pre_nyse", "eod_institutional".

        Returns:
            ArtifactBundle populated from all available artifact files.

        Raises:
            FileNotFoundError: If the session directory does not exist.
        """
        # Return cache if same session
        if (self._cached_bundle is not None
                and self._cached_date == date
                and self._cached_session == session):
            logger.debug("Returning cached bundle for %s/%s", date, session)
            return self._cached_bundle

        session_dir = self._base_dir / date / session
        if not session_dir.exists():
            raise FileNotFoundError(
                f"Artifact session directory not found: {session_dir}"
            )

        logger.info("Loading artifacts from %s", session_dir)

        # Load individual artifact files
        plays = self._load_json_list(session_dir / "plays.json")
        peer_plays = self._load_json_list(session_dir / "peers_intel.json")
        full_scan = self._load_json_list(session_dir / "full_scan.json")
        regime = self._load_json_dict(session_dir / "regime.json")
        data_health = self._load_json_dict(session_dir / "data_health.json")
        system_state = self._load_json_dict(session_dir / "system_state.json")
        drought = self._load_json_dict(session_dir / "drought.json")
        opportunity = self._load_json_list(session_dir / "opportunity.json")
        manifest_data = self._load_json_dict(session_dir / "manifest.json")

        # Build the bundle
        bundle = ArtifactBundle(
            date=date,
            session=session,
            plays=plays,
            peer_plays=peer_plays,
            full_scan=full_scan,
            regime=regime,
            data_health=data_health,
            system_state=system_state,
            drought=drought,
            opportunity=opportunity,
            truth_manifest=manifest_data,
        )

        # Verify truth manifest hash if available
        if manifest_data and plays:
            expected_hash = manifest_data.get("plays_hash", "")
            actual_hash = self.compute_plays_hash(plays)
            if expected_hash and expected_hash != actual_hash:
                logger.warning(
                    "PLAYS HASH MISMATCH for %s/%s: "
                    "manifest=%s, computed=%s -- possible stale artifact",
                    date, session, expected_hash[:16], actual_hash[:16],
                )

        # Cache
        self._cached_bundle = bundle
        self._cached_date = date
        self._cached_session = session

        logger.info(
            "Loaded %s/%s: %d plays, %d peers, regime=%s",
            date, session, len(plays), len(peer_plays),
            regime.get("tag", "N/A") if regime else "N/A",
        )
        return bundle

    def get_plays(self) -> list[dict]:
        """Return the plays from the currently cached bundle.

        Returns an empty list if no session is loaded.
        """
        if self._cached_bundle is None:
            return []
        return self._cached_bundle.plays

    def get_manifest(self) -> Optional[TruthManifest]:
        """Return the TruthManifest from the currently cached bundle.

        Returns None if no session is loaded or manifest is missing.
        """
        if self._cached_bundle is None:
            return None
        if self._cached_bundle.truth_manifest is None:
            return None
        try:
            return TruthManifest.from_dict(self._cached_bundle.truth_manifest)
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse cached TruthManifest: %s", e)
            return None

    def get_bundle(self) -> Optional[ArtifactBundle]:
        """Return the currently cached ArtifactBundle, or None."""
        return self._cached_bundle

    def clear_cache(self) -> None:
        """Clear the cached bundle (forces reload on next load_session())."""
        self._cached_bundle = None
        self._cached_date = ""
        self._cached_session = ""

    def list_sessions(self, date: str) -> list[str]:
        """List all available session directories for a given date.

        Args:
            date: Date string, e.g. "2026-02-26".

        Returns:
            Sorted list of session directory names.
        """
        date_dir = self._base_dir / date
        if not date_dir.exists():
            return []
        return sorted([
            d.name for d in date_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    def list_dates(self) -> list[str]:
        """List all available date directories under the base artifacts dir.

        Returns:
            Sorted list of date strings (directory names).
        """
        if not self._base_dir.exists():
            return []
        return sorted([
            d.name for d in self._base_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    # ------------------------------------------------------------------
    # Hash computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_plays_hash(plays: list[dict]) -> str:
        """Compute a deterministic SHA-256 hash of the plays list.

        The plays are serialised with sorted keys and no whitespace
        to ensure deterministic output regardless of dict ordering.

        Args:
            plays: List of play dicts (PlayCard-compatible).

        Returns:
            Hex digest of the SHA-256 hash.
        """
        canonical = json.dumps(plays, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_config_hash(config_path: str) -> str:
        """Compute SHA-256 hash of a configuration file.

        Args:
            config_path: Path to the configuration file (e.g. settings.yaml).

        Returns:
            Hex digest, or empty string if file cannot be read.
        """
        p = Path(config_path)
        if not p.exists():
            return ""
        try:
            content = p.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception as e:
            logger.warning("Failed to hash config %s: %s", config_path, e)
            return ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json_list(path: Path) -> list[dict]:
        """Load a JSON file expected to contain a list.

        Returns an empty list if the file doesn't exist or is malformed.
        """
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # Some artifact files wrap the list in a key
                # Try common patterns
                for key in ("plays", "candidates", "signals", "items", "data"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                # Single dict -- wrap in list
                return [data]
            else:
                logger.warning("Unexpected type in %s: %s", path, type(data).__name__)
                return []
        except json.JSONDecodeError as e:
            logger.warning("JSON parse error in %s: %s", path, e)
            return []
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            return []

    @staticmethod
    def _load_json_dict(path: Path) -> Optional[dict]:
        """Load a JSON file expected to contain a dict.

        Returns None if the file doesn't exist or is malformed.
        """
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("Expected dict in %s, got %s", path, type(data).__name__)
            return None
        except json.JSONDecodeError as e:
            logger.warning("JSON parse error in %s: %s", path, e)
            return None
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None
