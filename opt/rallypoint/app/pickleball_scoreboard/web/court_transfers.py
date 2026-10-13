"""Host-admin court transfers, durable jobs and a fixed paired-device inventory."""

import json
from uuid import UUID
from flask import jsonify, request, render_template, session
from sqlalchemy import select
from .models import Court, ScoreboardDevice, Match

ACTIVE = ("ACTIVE", "STARTING", "RESERVED")


def initialize_registry(host):
    with host.connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS physical_courts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, bridge_url TEXT UNIQUE NOT NULL, slug TEXT NOT NULL, device_id INTEGER NOT NULL, court_id INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS court_moves (id TEXT PRIMARY KEY, physical_id INTEGER NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )


def application(host, slug):
    app = host.root_app if slug == "legacy" else host.workspace(slug)
    if app is None:
        raise ValueError("Club no longer exists")
    return app


def clubs(host):
    return [
        {
            "slug": "legacy",
            "name": host.root_app.config.get(
                "CLUB_NAME", "Rally Point Pickleball Club"
            ),
        }
    ] + [{"slug": r["slug"], "name": r["name"]} for r in host.records()]


def idle(snapshot):
    if not snapshot.get("connected"):
        raise ValueError("Court is offline. Restore its connection before moving it.")
    if snapshot.get("match_exists", bool(snapshot.get("match_id"))) and snapshot.get(
        "state", {}
    ).get("status") not in ("complete", "ended"):
        raise ValueError(
            "A match is in progress. Finish or end it before moving this court."
        )
    if snapshot.get("sync", {}).get("pending", 0):
        raise ValueError(
            "Score updates are still synchronizing. Please try again shortly."
        )


def binding(host, physical_id):
    with host.connect() as db:
        row = db.execute(
            "SELECT * FROM physical_courts WHERE id=?", (physical_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Court is not paired for automatic switching")
    return dict(row)


def plan(host, physical_id, target_slug, number, end_match=False):
    physical = binding(host, int(physical_id))
    target = application(host, target_slug)
    if not 1 <= number <= 99:
        raise ValueError("Choose a court number between 1 and 99")
    source = application(host, physical["slug"])
    with source.extensions["database"].transaction() as s:
        court = s.get(Court, physical["court_id"])
        device = s.get(ScoreboardDevice, physical["device_id"])
        if not court or not device or device.bridge_url != physical["bridge_url"]:
            raise ValueError("Source registration changed; refresh this page")
        source_matches = list(s.scalars(
            select(Match.id).where(Match.court_id == court.id, Match.status.in_(ACTIVE))
        ))
        if source_matches and not end_match:
            raise ValueError(
                "This court has an active or reserved match. Finish or release it first."
            )
    snapshot = source.extensions["match_state"].request("/state", url=physical["bridge_url"])
    if end_match:
        if not snapshot.get("connected"):
            raise ValueError("Court is offline. Restore its connection before moving it.")
        if snapshot.get("sync", {}).get("pending", 0) or snapshot.get("sync", {}).get("online") is False:
            raise ValueError("Wait for score updates to synchronize before moving this court")
    else:
        idle(snapshot)
    with target.extensions["database"].transaction() as s:
        court = s.scalar(select(Court).where(Court.number == number))
        same = (
            physical["slug"] == target_slug
            and court is not None
            and physical["court_id"] == court.id
        )
        if court and not same:
            if court.active_match_id or s.scalar(
                select(Match.id).where(
                    Match.court_id == court.id, Match.status.in_(ACTIVE)
                )
            ):
                raise ValueError(
                    "The destination court has an active or reserved match"
                )
            device = s.scalar(
                select(ScoreboardDevice).where(ScoreboardDevice.court_id == court.id)
            )
            if device and device.bridge_url != physical["bridge_url"]:
                raise ValueError(
                    "The destination court already belongs to another device. Choose a different court number."
                )
    with host.connect() as db:
        active = db.execute(
            "SELECT 1 FROM court_moves WHERE physical_id=? AND status IN ('QUEUED','MOVING','NEEDS_REVIEW')",
            (physical["id"],),
        ).fetchone()
    if active:
        raise ValueError("This court already has a move awaiting completion or review")
    return {
        "physical_id": physical["id"],
        "name": physical["name"],
        "revision": physical["revision"],
        "source_slug": physical["slug"],
        "source_name": source.config.get("CLUB_NAME", "Rally Point Pickleball Club"),
        "target_slug": target_slug,
        "target_name": target.config.get("CLUB_NAME", "Rally Point Pickleball Club"),
        "court_number": number,
        "no_change": same,
        "end_match": bool(end_match),
        "expected_match_id": snapshot.get("match_id"),
        "source_matches": source_matches,
        "will_end_match": bool(end_match and (source_matches or (
            snapshot.get("match_exists", bool(snapshot.get("match_id")))
            and snapshot.get("state", {}).get("status") not in ("complete", "ended")))),
    }


def enqueue(host, values, actor):
    job_id = str(UUID(str(values.get("request_id", ""))))
    if values.get("confirmed") is not True:
        raise ValueError("Confirm the court move first")
    with host.connect() as db:
        old = db.execute("SELECT * FROM court_moves WHERE id=?", (job_id,)).fetchone()
        if old:
            return public_job(old)
    proposed = plan(
        host, values["physical_id"], values["target_slug"], int(values["court_number"]),
        end_match=values.get("end_match") is True,
    )
    if proposed["revision"] != int(values["revision"]):
        raise ValueError("Court assignment changed. Refresh and review the move again.")
    if proposed["no_change"]:
        raise ValueError("This court is already assigned here")
    if proposed["end_match"] and "expected_match_id" in values and values["expected_match_id"] != proposed["expected_match_id"]:
        raise ValueError("The court has a different match now. Review the move again.")
    proposed["actor"] = actor
    with host.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM network_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone():
            raise ValueError("A network change is pending. Wait before moving courts.")
        if db.execute(
            "SELECT 1 FROM court_moves WHERE physical_id=? AND status IN ('QUEUED','MOVING','NEEDS_REVIEW')",
            (proposed["physical_id"],),
        ).fetchone():
            raise ValueError("A move is already queued for this court")
        db.execute(
            "INSERT INTO court_moves(id,physical_id,payload,status,message) VALUES (?,?,?,?,?)",
            (
                job_id,
                proposed["physical_id"],
                json.dumps(proposed),
                "QUEUED",
                "Waiting for the court connection check",
            ),
        )
        row = db.execute("SELECT * FROM court_moves WHERE id=?", (job_id,)).fetchone()
        return public_job(row)


def public_job(row):
    values = dict(row)
    values["details"] = json.loads(values.pop("payload"))
    return values


def register_transfer_routes(host, app):
    initialize_registry(host)
    from .network_settings import register
    register(host, app)

    def authorized():
        return host.administrator(app)

    @app.get("/court-manager")
    def court_manager_page():
        if not authorized():
            from flask import redirect

            return redirect("/login?next=/court-manager")
        return render_template(
            "page.html",
            page="court-manager",
            csrf=session["csrf"],
            role=session.get("role"),
        )

    @app.get("/api/preview-assignment/<int:physical_id>")
    def preview_assignment(physical_id):
        """Public preview routing only; expose no device secrets or addresses."""
        with host.connect() as db:
            row = db.execute("SELECT slug,revision FROM physical_courts WHERE id=?", (physical_id,)).fetchone()
            moving = db.execute("SELECT 1 FROM court_moves WHERE physical_id=? AND status IN ('QUEUED','MOVING','NEEDS_REVIEW')", (physical_id,)).fetchone()
        if row is None:
            return jsonify(error="Court is not paired"), 404
        if moving:
            return jsonify(error="Court transfer in progress"), 409
        target = application(host, row["slug"])
        if not target.config.get("PUBLIC_LIVE", False):
            return jsonify(error="Public preview is disabled for this club"), 403
        response = jsonify(club=row["slug"], revision=row["revision"])
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/court-manager")
    def court_manager_inventory():
        if not authorized():
            return (
                jsonify(error="Sign in to the main workspace as a host administrator"),
                403,
            )
        with host.connect() as db:
            inventory = [
                dict(r) for r in db.execute("SELECT * FROM physical_courts ORDER BY id")
            ]
            jobs = [
                public_job(r)
                for r in db.execute(
                    "SELECT * FROM court_moves ORDER BY created_at DESC,rowid DESC LIMIT 20"
                )
            ]
        names = {c["slug"]: c["name"] for c in clubs(host)}
        for physical in inventory:
            physical["club_name"] = names.get(physical["slug"], physical["slug"])
            with application(host, physical["slug"]).extensions[
                "database"
            ].transaction() as s:
                court = s.get(Court, physical["court_id"])
                physical["court_number"] = court.number if court else None
            try:
                snapshot = (
                    application(host, physical["slug"])
                    .extensions["match_state"]
                    .request("/state", url=physical["bridge_url"])
                )
                physical["can_override"] = bool(snapshot.get("connected") and not snapshot.get("sync", {}).get("pending", 0)
                                                and snapshot.get("sync", {}).get("online") is not False)
                idle(snapshot)
                physical["ready"] = True
                physical["message"] = "Ready to move"
            except (ValueError, OSError) as exc:
                physical["ready"] = False
                physical["message"] = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Offline — check power and network"
                )
        return jsonify(courts=inventory, clubs=clubs(host), jobs=jobs)

    @app.get("/api/court-manager/plan")
    def court_move_plan():
        if not authorized():
            return jsonify(error="Host administrator access required"), 403
        return jsonify(
            plan(
                host,
                int(request.args["physical_id"]),
                request.args["target_slug"],
                int(request.args["court_number"]),
                end_match=request.args.get("end_match") == "true",
            )
        )

    @app.post("/api/court-manager/moves")
    def court_move_create():
        if not authorized():
            return jsonify(error="Host administrator access required"), 403
        return (
            jsonify(
                enqueue(
                    host,
                    request.get_json() or {},
                    "host-admin:" + str(session["user_id"]),
                )
            ),
            202,
        )

    @app.post("/api/court-manager/moves/<job_id>/retry")
    def court_move_retry(job_id):
        if not authorized():
            return jsonify(error="Host administrator access required"), 403
        job_id = str(UUID(job_id))
        with host.connect() as db:
            row = db.execute(
                "SELECT * FROM court_moves WHERE id=?", (job_id,)
            ).fetchone()
            if not row or row["status"] != "NEEDS_REVIEW":
                raise ValueError("This move does not require recovery")
            db.execute(
                "UPDATE court_moves SET status='MOVING',message='Retrying connection recovery' WHERE id=?",
                (job_id,),
            )
        return jsonify(id=job_id, status="MOVING"), 202
