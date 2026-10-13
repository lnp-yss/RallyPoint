"""Render a representative scoreboard using the production UI."""

import argparse
import os
from pathlib import Path
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pickleball_scoreboard.app import Application


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()
    path = Path("artifacts")
    path.mkdir(exist_ok=True)
    app = Application(path / f"preview-{args.width}", True, True)
    app.settings.width, app.settings.height = args.width, args.height
    app.set_display()
    app.dialog = None
    app.setup.settings.players_a = "DOMINIC / CHEL"
    app.setup.settings.players_b = "JOHN / MARIA"
    app.start()
    app.engine.state.game.scores = {"A": 8, "B": 6}
    app.engine.state.game.first_service = False
    app.engine.state.games_won["A"] = 1
    app.run(5, path / f"scoreboard-{args.width}.png")


if __name__ == "__main__":
    main()
