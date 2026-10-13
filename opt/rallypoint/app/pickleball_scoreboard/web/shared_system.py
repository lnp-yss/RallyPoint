"""Shared host with database and authentication isolation per club workspace."""

from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
import json
import re
import sqlite3
import os

from flask import jsonify, render_template, request, session
from werkzeug.security import generate_password_hash
from werkzeug.wrappers import Response

from .application import create_app
from .models import User


class SharedSystem:
    """Mount complete club applications; IDs never cross a database boundary."""

    def __init__(self, overrides=None):
        self.root_app = create_app(overrides)
        self.root_app.extensions["database"].migrate()
        self.base = dict(self.root_app.config)
        self.directory = Path(self.base["DATA_ROOT"]) / "data" / "clubs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.registry_path = self.directory.parent / "shared_registry.sqlite3"
        fd = os.open(self.registry_path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.registry_path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            companion = Path(str(self.registry_path) + suffix)
            if companion.exists():
                companion.chmod(0o600)
        self.lock = RLock()
        self.apps = {}
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS clubs (slug TEXT PRIMARY KEY, name TEXT NOT NULL, config_json TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0)"
            )
        self.install_routes(self.root_app, platform=True)
        from .court_transfers import register_transfer_routes

        register_transfer_routes(self, self.root_app)

    def connect(self):
        connection = sqlite3.connect(self.registry_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def records(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM clubs WHERE ready=1 ORDER BY name,slug"
                )
            ]

    def workspace(self, slug):
        with self.lock:
            if slug in self.apps:
                return self.apps[slug]
            with self.connect() as db:
                row = db.execute(
                    "SELECT * FROM clubs WHERE slug=? AND ready=1", (slug,)
                ).fetchone()
            if row is None:
                return None
            config = json.loads(row["config_json"])
            app = create_app({**self.base, **config})
            app.extensions["database"].migrate()
            self.install_routes(app, platform=False)
            self.apps[slug] = app
            return app

    def create_club(self, values):
        slug = str(values.get("slug", "")).strip().lower()
        name = str(values.get("name", "")).strip()
        username = str(values.get("username", "")).strip()
        password = str(values.get("password", ""))
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", slug) or slug.endswith("-"):
            raise ValueError(
                "Club URL must be 2–40 lowercase letters, numbers or hyphens, starting with a letter"
            )
        if (
            not name
            or len(name) > 100
            or not username
            or len(username) > 80
            or len(password) < 12
        ):
            raise ValueError(
                "Enter a club name, administrator username and a password of at least 12 characters"
            )
        public = values.get("public_live", True)
        if type(public) is not bool:
            raise ValueError("Public preview must be true or false")
        root = self.directory / slug
        config = {
            "DATABASE_URL": "sqlite:///" + (root / "data/club.db").as_posix(),
            "DATA_ROOT": str(root),
            "SECRET_KEY": token_urlsafe(48),
            "BRIDGE_TOKEN": token_urlsafe(48),
            # Never inherit the legacy club's physical scoreboard connection.
            "BRIDGE_URL": "http://127.0.0.1:65534",
            "SESSION_COOKIE_NAME": "rallypoint_" + slug.replace("-", "_"),
            "SESSION_COOKIE_PATH": "/clubs/" + slug,
            "APPLICATION_ROOT": "/clubs/" + slug,
            "CLUB_NAME": name,
            "CLUB_SLUG": slug,
            "PUBLIC_LIVE": public,
        }
        with self.lock:
            with self.connect() as db:
                if db.execute("SELECT 1 FROM clubs WHERE slug=?", (slug,)).fetchone():
                    raise ValueError("That club URL is already registered")
                db.execute(
                    "INSERT INTO clubs(slug,name,config_json) VALUES (?,?,?)",
                    (slug, name, json.dumps(config)),
                )
            try:
                root.mkdir(parents=True, exist_ok=False)
                app = create_app({**self.base, **config})
                app.extensions["database"].migrate()
                with app.extensions["database"].transaction() as s:
                    s.add(
                        User(
                            username=username,
                            password_hash=generate_password_hash(password),
                            role="ADMIN",
                        )
                    )
                self.install_routes(app, platform=False)
                self.apps[slug] = app
                with self.connect() as db:
                    db.execute("UPDATE clubs SET ready=1 WHERE slug=?", (slug,))
            except Exception:
                # Keep the reserved URL/data for diagnosis; never publish a
                # partially provisioned workspace or silently reuse its data.
                raise
        return {
            "slug": slug,
            "name": name,
            "url": "/clubs/" + slug + "/",
            "public_live": public,
        }

    @staticmethod
    def administrator(app):
        with app.extensions["database"].transaction() as s:
            user = (
                s.get(User, session.get("user_id")) if session.get("user_id") else None
            )
            return bool(user and user.active and user.role == "ADMIN")

    def install_routes(self, app, platform=False):
        app.extensions["shared_system"] = self
        app.config["SHARED_ENABLED"] = True

        @app.context_processor
        def login_club_choices():
            if request.endpoint != "login_page":
                return {}
            choices = [{"name": self.root_app.config.get("CLUB_NAME", "Rally Point Pickleball Club"), "url": "/login"}]
            choices.extend({"name": row["name"], "url": "/clubs/" + row["slug"] + "/login"} for row in self.records())
            choices.append({"name": "Host Administration", "url": "/login?host=1"})
            selected = request.script_root + "/login"
            if platform and request.args.get("host") == "1":
                selected = "/login?host=1"
            return {"login_clubs": choices, "selected_login": selected}

        @app.get("/api/club-profile")
        def club_profile():
            return jsonify(
                name=app.config.get("CLUB_NAME", "RALLY POINT PICKLEBALL CLUB"),
                slug=app.config.get("CLUB_SLUG"),
                platform=platform,
            )

        if not platform:
            return

        @app.get("/clubs")
        def clubs_page():
            return render_template(
                "page.html",
                page="clubs",
                csrf=session["csrf"],
                role=session.get("role"),
            )

        @app.get("/api/clubs")
        def clubs_list():
            owner = self.administrator(app)
            clubs = [
                {
                    "slug": None,
                    "name": app.config.get("CLUB_NAME", "Rally Point Pickleball Club"),
                    "url": "/",
                    "public_live": app.config["PUBLIC_LIVE"],
                }
            ]
            for row in self.records():
                config = json.loads(row["config_json"])
                if owner or config["PUBLIC_LIVE"]:
                    clubs.append(
                        {
                            "slug": row["slug"],
                            "name": row["name"],
                            "url": "/clubs/" + row["slug"] + "/",
                            "public_live": config["PUBLIC_LIVE"],
                        }
                    )
            return jsonify(clubs=clubs, can_create=owner)

        @app.post("/api/clubs")
        def clubs_create():
            if not self.administrator(app):
                return jsonify(error="Host administrator access required"), 403
            return jsonify(self.create_club(request.get_json() or {})), 201

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        match = re.match(r"^/clubs/([a-z][a-z0-9-]{1,39})(/.*)?$", path)
        if path.startswith("/clubs/"):
            if not match:
                return Response("Club not found", status=404)(environ, start_response)
            app = self.workspace(match[1])
            if app is None:
                return Response("Club not found", status=404)(environ, start_response)
            if not match[2]:
                return Response(status=302, headers={"Location": path + "/"})(
                    environ, start_response
                )
            routed = dict(environ)
            routed.pop("rallypoint.host_admin", None)
            # Decode only the root app's signed cookie; recheck its user every request.
            with self.root_app.request_context(environ):
                with self.root_app.extensions["database"].transaction() as database_session:
                    user = database_session.get(User, session.get("user_id")) if session.get("user_id") else None
                    if user and user.active and user.role == "ADMIN":
                        routed["rallypoint.host_admin"] = {"id": user.id, "username": user.username, "csrf": session.get("csrf", "")}

            routed["SCRIPT_NAME"] = (
                environ.get("SCRIPT_NAME", "") + "/clubs/" + match[1]
            )
            routed["PATH_INFO"] = match[2]
            return app(routed, start_response)
        return self.root_app(environ, start_response)
