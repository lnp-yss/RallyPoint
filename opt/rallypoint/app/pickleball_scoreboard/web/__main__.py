"""Development server and database/admin bootstrap commands."""

import argparse
from getpass import getpass
from werkzeug.security import generate_password_hash
from sqlalchemy import select
from . import create_app
from .models import User, Court, ScoreboardDevice


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--create-admin", metavar="USERNAME")
    parser.add_argument("--register-device", metavar="NAME")
    parser.add_argument("--court", type=int)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app = create_app()
    db = app.extensions["database"]
    if args.init_db:
        db.migrate()
        print("Schema version 3 installed; existing records preserved")
        return
    if args.create_admin:
        password = getpass("New admin password (12+ characters): ")
        if len(password) < 12:
            raise SystemExit("Password must be at least 12 characters")
        with db.transaction() as s:
            if s.scalar(select(User).where(User.username == args.create_admin)):
                raise SystemExit("Username already exists")
            s.add(
                User(
                    username=args.create_admin,
                    password_hash=generate_password_hash(password),
                    role="ADMIN",
                )
            )
        print("Admin created; sign in at /login")
        return
    if args.register_device:
        if not args.court:
            raise SystemExit("Specify --court with the database court ID")
        with db.transaction() as s:
            if not s.get(Court, args.court):
                raise SystemExit("Court not found")
            s.add(
                ScoreboardDevice(
                    device_name=args.register_device,
                    court_id=args.court,
                    bridge_url=app.config["BRIDGE_URL"],
                )
            )
        print("Device registered")
        return
    if app.config["DATABASE_URL"].startswith("sqlite:"):
        db.migrate()
    from werkzeug.serving import run_simple
    from .shared_system import SharedSystem
    run_simple("127.0.0.1", args.port, SharedSystem(dict(app.config)), threaded=True)


if __name__ == "__main__":
    main()
