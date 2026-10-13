"""Explicit SQLite-only demo data; never runs during production installation."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pickleball_scoreboard.web import create_app
from pickleball_scoreboard.web.models import User, Court
from werkzeug.security import generate_password_hash
from sqlalchemy import select


def main():
    app = create_app()
    if not app.config["DATABASE_URL"].startswith("sqlite:"):
        raise SystemExit("Demo seeding is restricted to a SQLite development database")
    db = app.extensions["database"]
    db.migrate()
    with db.transaction() as s:
        if not s.scalar(select(User).where(User.username == "demo")):
            s.add(
                User(
                    username="demo",
                    password_hash=generate_password_hash("local-demo-password"),
                    role="ADMIN",
                )
            )
        for number in (2, 3):
            if not s.scalar(select(Court).where(Court.number == number)):
                s.add(Court(name=f"Court {number}", number=number))
    club = app.extensions["club"]
    with db.transaction() as s:
        from pickleball_scoreboard.web.models import Player

        empty = not s.scalar(select(Player.id).limit(1))
    if empty:
        for name, rating in [
            ("Dominic", 3.8),
            ("Chel", 3.4),
            ("Mark", 3.7),
            ("Anna", 3.5),
            ("James", 3.2),
            ("Maria", 3.6),
        ]:
            player = club.save_player({"display_name": name, "skill_rating": rating})
            club.checkin(player["id"])
    print("Local development demo ready. Sign in: demo / local-demo-password")


if __name__ == "__main__":
    main()
