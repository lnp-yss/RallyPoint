"""Atomic versioned snapshots including bounded undo history."""

import json
import logging
import os
import tempfile
from pathlib import Path
from ..config import Settings
from ..scoring.game_state import MatchState
from ..scoring.scoring_engine import ScoringEngine


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Persistence:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.error = ""

    def write(self, name, data):
        try:
            atomic_json(self.root / name, data)
            self.error = ""
            return True
        except OSError as exc:
            self.error = str(exc)
            logging.exception("Save failed")
            return False

    def read(self, name):
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logging.exception("Unreadable JSON: %s", name)
            return None

    def settings(self) -> Settings:
        try:
            data = self.read("settings.json")
            return Settings(**data).validate() if data else Settings()
        except (ValueError, TypeError, KeyError):
            logging.exception("Invalid settings; defaults used")
            return Settings()

    def save(self, engine: ScoringEngine, context: dict | None = None) -> bool:
        return self.write(
            "current_match.json",
            {
                "version": 1,
                "external_context": context or getattr(engine, "external_context", {}),
                "settings": engine.settings.to_dict(),
                "state": engine.state.to_dict(),
                "history": [state.to_dict() for state in engine.history.states],
            },
        )

    def restore(self) -> ScoringEngine | None:
        data = self.read("current_match.json")
        if not data:
            return None
        try:
            if data["version"] != 1:
                raise ValueError("Unsupported save version")
            settings = Settings(**data["settings"]).validate()
            engine = ScoringEngine(settings, MatchState.from_dict(data["state"]))
            context = data.get("external_context", {})
            if not isinstance(context, dict) or not isinstance(
                context.get("applied_requests", []), list
            ):
                context = {}
            engine.external_context = context
            for state in data.get("history", [])[-100:]:
                engine.history.push(MatchState.from_dict(state))
            engine.restore_service_sides()
            return engine
        except (KeyError, ValueError, TypeError, AttributeError):
            logging.exception("Invalid saved match; starting setup")
            return None
