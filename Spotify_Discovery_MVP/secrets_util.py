"""Load app secrets from .streamlit/secrets.toml, Streamlit, or env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parent
_SECRETS_FILE = _APP_DIR / ".streamlit" / "secrets.toml"

_SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI",
)


def _read_toml_file(path: Path) -> dict[str, Any]:
    try:
        import tomllib

        with path.open("rb") as handle:
            return tomllib.load(handle)
    except Exception:
        import toml

        with path.open(encoding="utf-8") as handle:
            return toml.load(handle)


def load_app_secrets() -> dict[str, str]:
    """Merge secrets from local file, Streamlit secrets, and environment."""
    merged: dict[str, str] = {}

    if _SECRETS_FILE.exists():
        try:
            for key, value in _read_toml_file(_SECRETS_FILE).items():
                if value is not None and str(value).strip():
                    merged[key] = str(value).strip()
        except Exception:
            pass

    try:
        import streamlit as st

        for key in _SECRET_KEYS:
            try:
                if key in st.secrets:
                    value = st.secrets[key]
                    if value is not None and str(value).strip():
                        merged[key] = str(value).strip()
            except Exception:
                continue
    except Exception:
        pass

    for key in _SECRET_KEYS:
        env_val = os.environ.get(key)
        if env_val and env_val.strip():
            merged[key] = env_val.strip()

    return merged


def has_spotify_credentials(secrets: dict[str, str] | None = None) -> bool:
    data = secrets or load_app_secrets()
    return bool(data.get("SPOTIFY_CLIENT_ID") and data.get("SPOTIFY_CLIENT_SECRET"))
