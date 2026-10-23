#!/usr/bin/env python3
"""Unified credential management for LibreView and LibreLinkUP.

Manages two config files:
  - librelinkup_config.json (existing): email, password, region — LibreLinkUP caregiver API
  - libreview_config.json (new): email, password — LibreView patient portal

Functions: is_api_configured(), is_pdf_configured(), configure_api(), configure_pdf()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Paths — same directory as librelinkup_config.json (project root)
SCRIPT_DIR = Path(__file__).parent.resolve()
API_CONFIG_PATH = SCRIPT_DIR / "librelinkup_config.json"
PDF_CONFIG_PATH = SCRIPT_DIR / "libreview_config.json"
ENV_PATH = SCRIPT_DIR / ".env"

# ── .env loader (lightweight — no dependency on python-dotenv) ─────────────


def _load_env() -> dict[str, str]:
    """Load key=value pairs from .env file. Returns empty dict on failure."""
    env: dict[str, str] = {}
    if not ENV_PATH.exists():
        return env
    try:
        with open(ENV_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except Exception as exc:
        logger.warning(f"Failed to load .env: {exc}")
    return env


_ENV_CACHE = _load_env()


def _load_json(path: Path) -> dict:
    """Load a JSON config file, return empty dict if missing."""
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


# ── LibreLinkUP API config ────────────────────────────────────────────────


def get_api_config() -> dict:
    """Load LibreLinkUP credentials. Returns {} if not configured."""
    return _load_json(API_CONFIG_PATH)


def is_api_configured() -> bool:
    """Check if LibreLinkUP credentials are set."""
    cfg = get_api_config()
    return bool(cfg.get("email") and cfg.get("password"))


def configure_api(email: str, password: str, region: str = "latam") -> dict:
    """Write LibreLinkUP config AND .env. Returns the config that was written."""
    cfg = {
        "email": email.strip(),
        "password": password.strip(),
        "region": region.strip(),
    }
    with open(API_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)
    _sync_env(cfg, "api")
    logger.info(f"LibreLinkUP config written to {API_CONFIG_PATH}")
    return cfg


# ── LibreView PDF config ──────────────────────────────────────────────────


def get_pdf_config() -> dict:
    """Load LibreView credentials. Returns {} if not configured."""
    return _load_json(PDF_CONFIG_PATH)


def is_pdf_configured() -> bool:
    """Check if LibreView credentials are set."""
    cfg = get_pdf_config()
    return bool(cfg.get("email") and cfg.get("password"))


def _sync_env(cfg: dict, kind: str) -> None:
    """Write relevant credentials to .env file alongside JSON config.

    Preserves all other .env entries (PDF_DIR, etc.) unchanged.
    """
    try:
        prefix = "LIBRELINKUP" if kind == "api" else "LIBREVIEW"

        # Build fully-qualified key -> value map for this kind
        env_keys: dict[str, str] = {}
        for k, v in cfg.items():
            env_keys[f"{prefix}_{k.upper()}"] = v
        if kind == "api" and "region" in cfg:
            env_keys[f"{prefix}_REGION"] = cfg["region"]

        # Read existing .env to preserve other entries
        existing: dict[str, str] = {}
        if ENV_PATH.exists():
            with open(ENV_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    existing[key.strip()] = val.strip()

        # Merge: our keys overwrite, everything else preserved
        existing.update(env_keys)

        # Write back
        header = ("# LibreLinkUP caregiver API credentials" if kind == "api"
                  else "# LibreView patient portal credentials")
        with open(ENV_PATH, "w") as f:
            f.write(f"{header}\n")
            for key, val in existing.items():
                # Skip the prefixed keys we'll write explicitly below
                if key.startswith(prefix + "_"):
                    continue
                f.write(f"{key}={val}\n")
            # Write our managed keys
            for key in sorted(env_keys.keys()):
                f.write(f"{key}={env_keys[key]}\n")

        # Invalidate cache
        global _ENV_CACHE
        _ENV_CACHE = _load_env()
    except Exception as exc:
        logger.warning(f"Failed to sync .env: {exc}")


def configure_pdf(email: str, password: str) -> dict:
    """Write LibreView config AND .env. Returns the config that was written."""
    cfg = {
        "email": email.strip(),
        "password": password.strip(),
    }
    with open(PDF_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)
    _sync_env(cfg, "pdf")
    logger.info(f"LibreView config written to {PDF_CONFIG_PATH}")
    return cfg


# ── .env helpers ───────────────────────────────────────────────────────────


def _get_env(key: str) -> Optional[str]:
    """Read a single value from the cached .env."""
    return _ENV_CACHE.get(key)


def get_libreview_pdf_dir() -> Optional[str]:
    """Return LIBREVIEW_PDF_DIR from .env."""
    return _ENV_CACHE.get("LIBREVIEW_PDF_DIR")


# ── Status ─────────────────────────────────────────────────────────────────


def get_status() -> dict:
    """Return configuration status for both sources."""
    return {
        "api_configured": is_api_configured(),
        "pdf_configured": is_pdf_configured(),
        "api_config": get_api_config(),
        "pdf_config": get_pdf_config(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Status:", json.dumps(get_status(), indent=2))
