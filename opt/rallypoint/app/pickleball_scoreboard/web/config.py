"""Environment-owned web configuration, independent of scoring settings."""

import os
from pathlib import Path


def configuration() -> dict:
    root = Path(
        os.environ.get("RALLYPOINT_DATA_ROOT", Path(__file__).resolve().parents[2])
    )
    return {
        "DATABASE_URL": os.environ.get(
            "RALLYPOINT_DATABASE_URL", f'sqlite:///{(root / "data/club.db").as_posix()}'
        ),
        "SECRET_KEY": os.environ.get("RALLYPOINT_SECRET_KEY", ""),
        "BRIDGE_URL": os.environ.get("RALLYPOINT_BRIDGE_URL", "http://127.0.0.1:8765"),
        "BRIDGE_TOKEN": os.environ.get("RALLYPOINT_BRIDGE_TOKEN", ""),
        "HUB_PUBLIC_URL": os.environ.get("RALLYPOINT_HUB_PUBLIC_URL", ""),
        "PUBLIC_LIVE": os.environ.get("RALLYPOINT_PUBLIC_LIVE", "true").lower()
        == "true",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.environ.get("RALLYPOINT_HTTPS", "false") == "true",
        "MAX_CONTENT_LENGTH": 65536,
        "DATA_ROOT": str(root),
    }
