"""Flask application factory; web imports never require Pygame."""

from datetime import timedelta
from functools import wraps
from pathlib import Path
from secrets import token_urlsafe
from time import monotonic, sleep
import hmac
import json
import logging
from flask import (
    Flask,
    jsonify,
    request,
    session,
    redirect,
    render_template,
    Response,
    stream_with_context,
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from .auth_context import host_identity, effective_role
from .config import configuration
from .database import Database
from .models import (
    User,
    Player,
    Court,
    Match,
    MatchPlayer,
    MatchEvent,
    Game,
    ScoreboardDevice,
)
from .club_service import ClubService, row_dict
from .state_service import MatchStateService


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(configuration())
    app.config.update(overrides or {})
    if not app.config["SECRET_KEY"]:
        keyfile = Path(app.config["DATA_ROOT"]) / "data/web_session.key"
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            with keyfile.open("x", encoding="ascii") as f:
                f.write(token_urlsafe(48))
            keyfile.chmod(0o600)
        except FileExistsError:
            pass
        app.config["SECRET_KEY"] = keyfile.read_text().strip()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    if not app.config.get("TESTING") and len(app.config["SECRET_KEY"]) < 32:
        raise ValueError("Use a persistent session secret of at least 32 characters")
    db = Database(app.config["DATABASE_URL"])
    club = ClubService(db, app.config.get("CLUB_NAME"))
    state = MatchStateService(club, app.config)
    app.extensions.update(club=club, database=db, match_state=state)

    @app.context_processor
    def workspace_context():
        identity = host_identity()
        active_username = identity["username"] + " Â· Host Admin" if identity else None
        if not identity and session.get("user_id"):
            with db.transaction() as database_session:
                user = database_session.get(User, session["user_id"])
                if user and user.active:
                    active_username = user.username
        return {
            "club_name": app.config.get("CLUB_NAME", "RALLY POINT PICKLEBALL CLUB"),
            "active_username": active_username,
            "host_access": identity,
            "host_admin": bool(identity or (app.extensions.get("shared_system") and app.extensions["shared_system"].root_app is app and effective_role() == "ADMIN")),
        }

    @app.after_request
    def workspace_redirect(response):
        location = response.headers.get("Location", "")
        if (
            request.script_root
            and location.startswith("/")
            and not location.startswith("//")
        ):
            if not location.startswith(request.script_root + "/"):
                response.headers["Location"] = request.script_root + location
        return response

    failures = {}
    from threading import Lock
    from copy import deepcopy

    courtside_cache = {"at": 0, "data": None}
    courtside_lock = Lock()

    def permitted(*roles):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if host_identity():
                    return fn(*args, **kwargs)
                with db.transaction() as s:
                    user = (
                        s.get(User, session.get("user_id"))
                        if session.get("user_id")
                        else None
                    )
                    if not user or not user.active:
                        return (
                            (jsonify(error="Login required"), 401)
                            if request.path.startswith("/api/")
                            else redirect("/login")
                        )
                    if user.role not in roles:
                        return (
                            jsonify(error="This role cannot perform this action"),
                            403,
                        )
                return fn(*args, **kwargs)

            return wrapped

        return decorator

    operator = permitted("ADMIN", "OPERATOR")
    viewer = permitted("ADMIN", "OPERATOR", "VIEWER")
    admin = permitted("ADMIN")

    @app.before_request
    def csrf_protection():
        session.setdefault("csrf", token_urlsafe(32))
        if request.endpoint == "device_ingest":
            token = app.config.get("BRIDGE_TOKEN", "")
            if len(token) < 32 or not hmac.compare_digest(
                request.headers.get("Authorization", ""), "Bearer " + token
            ):
                return jsonify(error="Unauthorized"), 401
            return
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not hmac.compare_digest(token, session["csrf"]):
                return jsonify(error="Invalid CSRF token; reload the page"), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def bad_input(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(IntegrityError)
    def conflict(exc):
        logging.warning("Club operation conflict: %s", type(exc).__name__)
        return (
            jsonify(error="Record conflicts with an existing assignment or name"),
            409,
        )

    @app.errorhandler(SQLAlchemyError)
    def db_unavailable(exc):
        logging.warning("Club database unavailable: %s", type(exc).__name__)
        return (
            jsonify(error="Club database is unavailable. HDMI scoring continues."),
            503,
        )

    @app.errorhandler(OSError)
    def ipc_unavailable(exc):
        return (
            jsonify(
                error="Scoreboard bridge is offline; HDMI scoring remains independent"
            ),
            503,
        )

    @app.get("/login")
    def login_page():
        return render_template("login.html", csrf=session["csrf"], host_login=bool(app.config.get("SHARED_ENABLED") and not request.script_root and request.args.get("host") == "1"))

    @app.post("/login")
    def login():
        address = request.remote_addr
        now = monotonic()
        prior = [stamp for stamp in failures.get(address, []) if now - stamp < 60]
        if len(prior) >= 5:
            return jsonify(error="Too many attempts; try again in one minute"), 429
        values = request.get_json(silent=True) or request.form
        with db.transaction() as s:
            user = s.scalar(
                select(User).where(User.username == values.get("username", ""))
            )
            if (
                not user
                or not user.active
                or not check_password_hash(
                    user.password_hash, values.get("password", "")
                )
            ):
                prior.append(now)
                failures[address] = prior
                return jsonify(error="Invalid username or password"), 401
            if values.get("host_admin") == "1" and (
                request.script_root or user.role != "ADMIN"
            ):
                prior.append(now)
                failures[address] = prior
                return jsonify(error="Host administrator account required"), 403
            session.clear()
            session.update(user_id=user.id, csrf=token_urlsafe(32), role=user.role)
            session.permanent = True
        return redirect("/live" if user.role == "VIEWER" else "/")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect("/login")

    @app.get("/api/session")
    def whoami():
        return jsonify(
            csrf=session["csrf"],
            role=effective_role(),
            public_live=app.config["PUBLIC_LIVE"],
        )

    @app.get("/")
    @operator
    def dashboard():
        return render_template(
            "page.html",
            page="dashboard",
            csrf=session["csrf"],
            role=effective_role(),
        )

    def page_view(page):
        return render_template(
            "page.html", page=page, csrf=session["csrf"], role=effective_role()
        )

    for page in ("setup", "control", "players", "queue", "checkin", "courts"):
        app.add_url_rule(
            "/" + page,
            endpoint="page_" + page,
            view_func=operator(lambda page=page: page_view(page)),
        )
    app.add_url_rule(
        "/settings", endpoint="settings", view_func=admin(lambda: page_view("settings"))
    )
    app.add_url_rule(
        "/matches", endpoint="matches", view_func=viewer(lambda: page_view("matches"))
    )

    @app.get("/live")
    def live():
        if not app.config["PUBLIC_LIVE"]:
            return viewer(lambda: page_view("live"))()
        return page_view("live")

    @app.get("/preview")
    def preview():
        if not app.config["PUBLIC_LIVE"]:
            return viewer(lambda: page_view("preview"))()
        return page_view("preview")

    @app.get("/api/preview")
    def preview_state():
        from ..config import Settings
        from ..scoring.scoring_engine import ScoringEngine
        from ..services.presentation import indicators

        settings = Settings(
            team_a=request.args.get("team_a", "TEAM A")[:100],
            team_b=request.args.get("team_b", "TEAM B")[:100],
            players_a=request.args.get("players_a", "DOMINIC / CHEL")[:100],
            players_b=request.args.get("players_b", "MARK / ANNA")[:100],
            court_name="COURT PREVIEW",
        )
        settings.best_of = request.args.get("best_of", 3, type=int)
        settings.validate()
        state = ScoringEngine(settings).state.to_dict()
        game = state["game"]
        game["scores"] = {
            team: max(0, min(999, request.args.get(key, default, type=int)))
            for team, key, default in [("A", "a", 8), ("B", "b", 6)]
        }
        game["serving"] = "B" if request.args.get("serving") == "B" else "A"
        game["server"] = 1 if request.args.get("server") == "1" else 2
        game["timeouts"] = {
            team: max(
                0, min(settings.timeout_limit, request.args.get(key, 0, type=int))
            )
            for team, key in [("A", "timeouts_a"), ("B", "timeouts_b")]
        }
        state["game_number"] = max(1, request.args.get("game", 1, type=int))
        winner = request.args.get("winner")
        if winner in ("A", "B"):
            state.update(status="complete", winner=winner)
        snapshot = {
            "settings": settings.to_dict(),
            "state": state,
            "connected": True,
            "revision": 1,
            "match_id": None,
        }
        snapshot["indicators"] = indicators(snapshot)
        return jsonify(snapshot)

    @app.get("/leaderboard")
    def leaderboard_page():
        if not app.config["PUBLIC_LIVE"]:
            return viewer(lambda: page_view("leaderboard"))()
        return page_view("leaderboard")

    @app.get("/api/leaderboard")
    def leaderboard_data():
        if not app.config["PUBLIC_LIVE"]:
            return viewer(lambda: jsonify(club.leaderboard(request.args.get("event", type=int), request.args.get("scope") == "all")))()
        return jsonify(club.leaderboard(request.args.get("event", type=int), request.args.get("scope") == "all"))

    @app.get("/play-events")
    @operator
    def play_events_page():
        return page_view("play-events")

    @app.get("/api/dashboard")
    @operator
    def dashboard_data():
        excluded = session.get("skipped", [])
        return jsonify(club.dashboard(request.args.get("type") == "Singles", excluded))

    @app.post("/api/suggestions/skip")
    @operator
    def skip():
        ids = sorted(int(pid) for pid in request.json["players"])
        session["skipped"] = (session.get("skipped", []) + [ids])[-10:]
        return jsonify(ok=True)

    @app.get("/api/players")
    @operator
    def players():
        query = request.args.get("q", "")[:100]
        with db.transaction() as s:
            rows = s.scalars(
                select(Player)
                .where(Player.display_name.contains(query))
                .order_by(Player.display_name)
                .limit(300)
            )
            return jsonify([row_dict(p) for p in rows])

    @app.post("/api/players")
    @admin
    def create_player():
        return jsonify(club.save_player(request.json)), 201

    @app.post("/api/players/quick-checkin")
    @operator
    def quick_checkin_player():
        return jsonify(club.quick_checkin_player(request.json or {})), 201

    @app.post("/api/players/<int:player_id>")
    @admin
    def edit_player(player_id):
        return jsonify(club.save_player(request.json, player_id))

    @app.get("/api/players/<int:player_id>")
    @operator
    def profile(player_id):
        with db.transaction() as s:
            player = s.get(Player, player_id)
            if not player:
                return jsonify(error="Player not found"), 404
            return jsonify(
                player=row_dict(player), statistics=club.statistics(s, player_id)
            )

    @app.post("/api/checkin")
    @operator
    def checkin():
        return jsonify(club.checkin(request.json["player_id"]))

    @app.post("/api/queue/<int:player_id>")
    @operator
    def queue_action(player_id):
        club.queue_action(player_id, request.json["action"])
        return jsonify(ok=True)

    @app.get("/api/courts")
    @viewer
    def courts():
        return jsonify(club.rotation.courts())

    @app.post("/api/courts")
    @admin
    def court_create():
        values = request.json
        with db.transaction() as s:
            court = Court(name=str(values["name"])[:100], number=int(values["number"]))
            s.add(court)
            s.flush()
            return jsonify(row_dict(court)), 201

    @app.post("/api/courts/<int:court_id>")
    @admin
    def court_edit(court_id):
        values = request.json
        with db.assignment_transaction() as s:
            court = s.get(Court, court_id, with_for_update=True)
            if not court or court.active_match_id:
                raise ValueError("Court is not editable while assigned")
            if club.rotation.has_hold(s, court.id):
                raise ValueError("Court has retained winners")
            if values.get("status") not in (
                "AVAILABLE",
                "RESERVED",
                "MAINTENANCE",
                "DISABLED",
            ):
                raise ValueError("Invalid court status")
            court.status = values["status"]
            court.enabled = int(court.status != "DISABLED")
            if court.status == "AVAILABLE":
                from .models import utcnow

                court.available_since = utcnow()
            court.name = str(values.get("name", court.name))[:100]
            return jsonify(row_dict(court))

    @app.post("/api/devices")
    @admin
    def devices():
        values = request.json
        state.device_url()  # Central URL validation also applies at call time.
        from urllib.parse import urlparse

        if urlparse(values.get("bridge_url", "")).hostname not in (
            "127.0.0.1",
            "localhost",
            "::1",
        ):
            raise ValueError("Use loopback IPC or an SSH tunnel for another device")
        with db.assignment_transaction() as s:
            device = (
                s.get(ScoreboardDevice, int(values["id"]))
                if values.get("id")
                else ScoreboardDevice()
            )
            if device is None:
                raise ValueError("Device not found")
            court = s.get(Court, int(values["court_id"]))
            if not court:
                raise ValueError("Court not found")
            if device.id and device.court_id != court.id:
                old_court = s.get(Court, device.court_id)
                if (old_court and old_court.active_match_id) or court.active_match_id:
                    raise ValueError("Finish the assigned match before moving a device")
            if not str(values.get("device_name", "")).strip():
                raise ValueError("Device name is required")
            for key in (
                "device_name",
                "court_id",
                "hostname",
                "ip_address",
                "bridge_url",
            ):
                if key in values:
                    setattr(device, key, values[key])
            s.add(device)
            s.flush()
            return jsonify(row_dict(device))

    @app.post("/api/devices/<int:device_id>/delete")
    @admin
    def delete_device(device_id):
        with db.transaction() as s:
            device = s.get(ScoreboardDevice, device_id, with_for_update=True)
            if not device:
                return jsonify(error="Device not found"), 404
            court = s.get(Court, device.court_id)
            if court and court.active_match_id:
                raise ValueError("Finish the assigned match before deleting its device")
            s.delete(device)
        return jsonify(ok=True)

    @app.post("/api/matches")
    @operator
    def prepare():
        return jsonify(state.prepare_match(request.json)), 201

    @app.post("/api/matches/<int:match_id>/start")
    @operator
    def start(match_id):
        return (
            jsonify(
                state.start_match(match_id, bool(request.json.get("replace_confirmed")))
            ),
            202,
        )

    @app.post("/api/courts/<int:court_id>/end-casual")
    @operator
    def end_casual(court_id):
        is_host = bool(host_identity() or (app.extensions.get("shared_system")
            and app.extensions["shared_system"].root_app is app and effective_role() == "ADMIN"))
        if not is_host:
            return jsonify(error="Host administrator access required"), 403
        values = request.get_json() or {}
        if values.get("confirmed") is not True:
            raise ValueError("Confirmation is required")
        url = state.device_url(court_id)
        live = state.request("/state", url=url)
        if not live.get("connected") or live.get("sync", {}).get("pending", 0):
            raise ValueError("Court is offline or still synchronizing")
        if live.get("settings", {}).get("play_mode") != "Casual Play" or live.get("match_id"):
            raise ValueError("This court is not running Casual Play")
        if not live.get("match_exists"):
            raise ValueError("No casual match is running")
        if values.get("expected_revision") != live.get("revision"):
            raise ValueError("Court changed; refresh before stopping Casual Play")
        from uuid import uuid4
        return jsonify(state.request("/commands", {
            "name": "END_MATCH", "request_id": str(uuid4()),
            "payload": {"match_id": None, "host_override": True,
                        "end_casual": True, "expected_revision": live["revision"],
                        "actor": staff_actor()}}, url)), 202

    @app.post("/api/matches/<int:match_id>/commands")
    @operator
    def command(match_id):
        return (
            jsonify(
                state.submit_command(
                    match_id,
                    request.json["name"],
                    request.json.get("confirmed") is True,
                    request.json.get("request_id"),
                    request.json.get("payload"),
                    actor=staff_actor(),
                    host_override=bool(
                        host_identity()
                        or (
                            app.extensions.get("shared_system")
                            and app.extensions["shared_system"].root_app is app
                            and effective_role() == "ADMIN"
                        )
                    ),
                )
            ),
            202,
        )

    @app.get("/api/receipts/<request_id>")
    @operator
    def receipt(request_id):
        return jsonify(
            state.request(
                "/receipts/" + request_id,
                url=state.device_url(request.args.get("court", type=int)),
            )
        )

    @app.post("/api/matches/<int:match_id>/release")
    @operator
    def release_reservation(match_id):
        if request.json.get("confirmed") is not True:
            raise ValueError("Confirmation required")
        with db.transaction() as s:
            match = s.get(Match, match_id)
            court_id = match.court_id if match else None
        live = state.get_current_state(court_id)
        if not live.get("connected") or live.get("match_id") == match_id:
            raise ValueError(
                "Cannot release until scoreboard is online and confirms this match did not start"
            )
        club.cancel_reservation(match_id)
        return jsonify(ok=True)

    @app.get("/api/matches")
    @viewer
    def match_list():
        query = select(Match)
        if request.args.get("event"):
            query = query.where(Match.session_id == int(request.args["event"]))
        if request.args.get("court"):
            query = query.where(Match.court_id == int(request.args["court"]))
        if request.args.get("type"):
            query = query.where(Match.match_type == request.args["type"])
        if request.args.get("date"):
            from datetime import datetime

            date = datetime.fromisoformat(request.args["date"])
            query = query.where(
                Match.created_at >= date, Match.created_at < date + timedelta(days=1)
            )
        if request.args.get("player"):
            query = query.where(
                Match.id.in_(
                    select(MatchPlayer.match_id).where(
                        MatchPlayer.player_id == int(request.args["player"])
                    )
                )
            )
        with db.transaction() as s:
            return jsonify(
                [
                    row_dict(m)
                    for m in s.scalars(query.order_by(Match.id.desc()).limit(200))
                ]
            )

    @app.get("/api/matches/<int:match_id>")
    @viewer
    def match_detail(match_id):
        with db.transaction() as s:
            match = s.get(Match, match_id)
            if not match:
                return jsonify(error="Match not found"), 404
            return jsonify(
                match=row_dict(match),
                games=[
                    row_dict(g)
                    for g in s.scalars(
                        select(Game)
                        .where(Game.match_id == match_id)
                        .order_by(Game.game_number)
                    )
                ],
                timeline=[
                    row_dict(e)
                    for e in s.scalars(
                        select(MatchEvent)
                        .where(MatchEvent.match_id == match_id)
                        .order_by(MatchEvent.sequence_no)
                    )
                ],
                players=[
                    {
                        **row_dict(link),
                        "display_name": s.get(Player, link.player_id).display_name,
                    }
                    for link in s.scalars(
                        select(MatchPlayer).where(MatchPlayer.match_id == match_id)
                    )
                ],
            )

    @app.get("/api/settings")
    @admin
    def system_options():
        with db.transaction() as s:
            return jsonify(
                users=[
                    dict(id=u.id, username=u.username, role=u.role)
                    for u in s.scalars(
                        select(User).where(User.active == 1).order_by(User.username)
                    )
                ],
                options=club.options(s),
                devices=[row_dict(d) for d in s.scalars(select(ScoreboardDevice))],
            )

    @app.post("/api/settings")
    @admin
    def update_options():
        club.set_options(request.json)
        return jsonify(ok=True)

    @app.post("/api/users")
    @admin
    def users():
        values = request.json
        if (
            values.get("role") not in ("ADMIN", "OPERATOR", "VIEWER")
            or len(values.get("password", "")) < 12
        ):
            raise ValueError("Choose a role and a password of at least 12 characters")
        with db.transaction() as s:
            user = User(
                username=values["username"],
                password_hash=generate_password_hash(values["password"]),
                role=values["role"],
            )
            s.add(user)
            s.flush()
            return jsonify(id=user.id, username=user.username, role=user.role), 201

    @app.post("/api/users/<int:user_id>")
    @admin
    def edit_user(user_id):
        values = request.json
        with db.transaction() as s:
            # Lock administrators together to serialize last-admin changes.
            admins = list(
                s.scalars(
                    select(User)
                    .where(User.active == 1, User.role == "ADMIN")
                    .with_for_update()
                )
            )
            user = s.get(User, user_id, with_for_update=True)
            if not user or not user.active:
                return jsonify(error="Staff account not found"), 404
            username = str(values.get("username", user.username)).strip()
            role = values.get("role", user.role)
            if (
                not username
                or len(username) > 80
                or role not in ("ADMIN", "OPERATOR", "VIEWER")
            ):
                raise ValueError("Enter a username and valid role")
            if user.role == "ADMIN" and role != "ADMIN" and len(admins) <= 1:
                raise ValueError("At least one administrator must remain")
            password = values.get("password", "")
            if not isinstance(password, str) or (password and len(password) < 12):
                raise ValueError("New password must contain at least 12 characters")
            if password:
                user.password_hash = generate_password_hash(password)
            user.username, user.role = username, role
        return jsonify(ok=True)

    @app.post("/api/users/<int:user_id>/delete")
    @admin
    def delete_user(user_id):
        with db.transaction() as s:
            admins = list(
                s.scalars(
                    select(User)
                    .where(User.active == 1, User.role == "ADMIN")
                    .with_for_update()
                )
            )
            user = s.get(User, user_id, with_for_update=True)
            if not user or not user.active:
                return jsonify(error="Staff account not found"), 404
            if not host_identity() and user.id == session.get("user_id"):
                raise ValueError("You cannot delete your own account")
            if user.role == "ADMIN" and len(admins) <= 1:
                raise ValueError("At least one administrator must remain")
            user.active = 0
        return jsonify(ok=True)

    def live_authorized():
        if host_identity() or app.config["PUBLIC_LIVE"]:
            return True
        with db.transaction() as s:
            user = (
                s.get(User, session.get("user_id")) if session.get("user_id") else None
            )
            return bool(
                user and user.active and user.role in ("ADMIN", "OPERATOR", "VIEWER")
            )

    @app.get("/api/live")
    def live_data():
        if not live_authorized():
            return jsonify(error="Login required"), 401
        return jsonify(state.get_current_state(request.args.get("court", type=int)))

    @app.get("/api/preview-announcements")
    def preview_announcements():
        if not live_authorized():
            return jsonify(error="Login required"), 401
        from .preview_announcements import pending_matches
        from .announcements import preview_voice
        from .score_sounds import recent_sounds, sound_settings
        sounds = sound_settings(db)
        return jsonify(matches=pending_matches(db), voice=preview_voice(db),
                       sounds=recent_sounds(db, sounds["output"]), score_sound_output=sounds["output"])

    @app.get("/api/courtside")
    def courtside_data():
        if not live_authorized():
            return jsonify(error="Login required"), 401
        from concurrent.futures import ThreadPoolExecutor

        with courtside_lock:
            if (
                courtside_cache["data"] is None
                or monotonic() - courtside_cache["at"] >= 5
            ):
                courtside_cache.update(at=monotonic(), data=club.courtside())
            data = deepcopy(courtside_cache["data"])
        # Read each Pi's authoritative state concurrently. A disconnected device
        # falls back to its persisted snapshot rather than hiding the other courts.
        if data["courts"]:
            with ThreadPoolExecutor(
                max_workers=min(4, len(data["courts"]))
            ) as executor:
                snapshots = list(
                    executor.map(
                        state.get_preview_state, [c["id"] for c in data["courts"]]
                    )
                )
            for court, snapshot in zip(data["courts"], snapshots):
                court["live"] = snapshot
        from .preview_display import display_settings
        data["display"] = display_settings(db)
        return jsonify(data)

    @app.get("/api/events")
    def events():
        if not live_authorized():
            return jsonify(error="Login required"), 401
        court_id = request.args.get("court", type=int)

        @stream_with_context
        def stream():
            previous = None
            # Timed stream renewal keeps role/session validation and thread use bounded.
            deadline = monotonic() + 25
            while monotonic() < deadline:
                try:
                    data = state.get_preview_state(court_id)
                except SQLAlchemyError:
                    data = {"connected": False, "error": "Database offline"}
                encoded = json.dumps(data, sort_keys=True)
                if encoded != previous:
                    yield "retry: 200\nevent: state\ndata: " + encoded + "\n\n"
                    previous = encoded
                else:
                    yield ": heartbeat\n\n"
                sleep(0.1)

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    from .operations import register_operations

    staff_actor = register_operations(
        app, db, club, state, admin, operator, live_authorized
    )
    from .rotation_routes import register_rotation

    register_rotation(app, club, state, operator)
    from .announcements import register as register_announcements
    register_announcements(app, db, operator, staff_actor)
    from .bluetooth_routes import register as register_bluetooth
    register_bluetooth(app, operator)
    from .preview_display import register as register_preview_display
    register_preview_display(app, db, operator)
    from .score_sounds import register as register_score_sounds
    register_score_sounds(app, db, operator)
    from .lineup import register as register_lineup
    register_lineup(app, db, club, state, operator, staff_actor)
    from .club_tools import register_club_tools

    register_club_tools(app, db, club, admin)
    from .event_service import register_events
    register_events(app, club, operator, viewer)

    from .tournaments import register_tournaments

    register_tournaments(app, db, club, operator)
    return app
