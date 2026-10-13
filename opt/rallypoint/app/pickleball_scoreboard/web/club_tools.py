"""Workspace-local roster import and authenticated device event ingestion."""

import csv
import hmac
from io import StringIO
from uuid import UUID
from flask import jsonify, request, Response
from sqlalchemy import select
from .models import Player, ScoreboardDevice


def register_club_tools(app, db, club, admin):
    @app.post("/api/players/import")
    @admin
    def import_roster():
        text = str((request.get_json() or {}).get("csv", "")).lstrip("\ufeff")
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames or "display_name" not in reader.fieldnames:
            raise ValueError(
                "CSV needs a display_name header; optional: first_name,last_name,nickname,gender,skill_rating,rating_type"
            )
        rows = list(reader)
        if not 1 <= len(rows) <= 500:
            raise ValueError("Import between 1 and 500 players at a time")
        prepared = []
        for number, row in enumerate(rows, 2):
            values = {
                k: (row.get(k) or "").strip()
                for k in (
                    "display_name",
                    "first_name",
                    "last_name",
                    "nickname",
                    "gender",
                    "skill_rating",
                    "rating_type",
                )
            }
            if (
                not values["display_name"]
                or any(
                    len(values[k]) > (100 if k == "display_name" else 80)
                    for k in ("display_name", "first_name", "last_name", "nickname")
                )
                or len(values["gender"]) > 30
            ):
                raise ValueError(f"CSV row {number}: missing or overlong name")
            values["rating_type"] = values["rating_type"] or "Manual"
            if values["rating_type"] not in ("Manual", "DUPR", "Self-rated"):
                raise ValueError(f"CSV row {number}: select Manual, DUPR or Self-rated")
            try:
                values["skill_rating"] = (
                    float(values["skill_rating"]) if values["skill_rating"] else None
                )
            except ValueError:
                raise ValueError(f"CSV row {number}: skill rating must be a number")
            if (
                values["skill_rating"] is not None
                and not 1 <= values["skill_rating"] <= 8
            ):
                raise ValueError(
                    f"CSV row {number}: skill rating must be between 1 and 8"
                )
            prepared.append(values)
        imported, skipped = 0, 0
        with db.assignment_transaction() as s:
            names = {name.casefold() for name in s.scalars(select(Player.display_name))}
            for values in prepared:
                key = values["display_name"].casefold()
                if key in names:
                    skipped += 1
                    continue
                s.add(Player(**values, status="ACTIVE"))
                names.add(key)
                imported += 1
        return jsonify(imported=imported, skipped=skipped)

    @app.post("/api/device-events")
    def device_ingest():
        # Also checked before the CSRF exemption in application.py.
        token = app.config.get("BRIDGE_TOKEN", "")
        if len(token) < 32 or not hmac.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + token
        ):
            return jsonify(error="Unauthorized"), 401
        event = request.get_json() or {}
        UUID(str(event.get("event_id", "")))
        if not event.get("device_id") or not isinstance(event.get("snapshot"), dict):
            raise ValueError("A registered device and snapshot are required")
        club.publish_state(event)
        from .lineup import device_roster
        return jsonify(accepted=True, branding=club.display_branding(), roster=device_roster(db, event))

    @app.get("/api/devices/<int:device_id>/configuration")
    @admin
    def device_configuration(device_id):
        with db.transaction() as s:
            device = s.get(ScoreboardDevice, device_id)
            if device is None:
                raise ValueError("Device not found")
            court_id = device.court_id
        token = app.config.get("BRIDGE_TOKEN", "")
        if len(token) < 32:
            raise ValueError("Host device token is not configured")
        body = (
            "\n".join(
                [
                    "# Private device configuration: install as /etc/rallypoint/client.env",
                    "# Keep permissions 0640 root:rallypoint. Configure its SSH reverse tunnel separately.",
                    "RALLYPOINT_BRIDGE=1",
                    "RALLYPOINT_BRIDGE_PORT=8765",
                    "RALLYPOINT_BRIDGE_TOKEN=" + token,
                    "RALLYPOINT_HUB_URL=" + (app.config.get("HUB_PUBLIC_URL") or request.host_url).rstrip("/") + request.script_root,
                    "RALLYPOINT_DEVICE_ID=" + str(device_id),
                    "RALLYPOINT_COURT_ID=" + str(court_id),
                    "# Remove RALLYPOINT_DATABASE_URL from this device when using HTTP sync.",
                ]
            )
            + "\n"
        )
        return Response(
            body,
            mimetype="text/plain",
            headers={"Content-Disposition": 'attachment; filename="client.env"'},
        )
