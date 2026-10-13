"""Operator-facing session, group and reservation endpoints."""
from .auth_context import host_identity, effective_role

from flask import jsonify, request, render_template, session


def register_rotation(app, club, state, operator):
    rotation = club.rotation
    app.extensions["rotation"] = rotation

    for page in ("courts", "matchmaking"):
        app.add_url_rule(
            "/settings/" + page,
            endpoint="rotation_page_" + page,
            view_func=operator(
                lambda page=page: render_template(
                    "page.html",
                    page="rotation-" + page,
                    csrf=session["csrf"],
                    role=effective_role(),
                )
            ),
        )

    @app.get("/api/rotation")
    @operator
    def rotation_configuration():
        return jsonify(
            **rotation.configuration(),
            courts=rotation.courts(),
            dashboard=club.dashboard()
        )

    @app.post("/api/rotation/settings")
    @operator
    def rotation_settings():
        return jsonify(rotation.save_configuration(request.get_json()))

    @app.post("/api/rotation/courts")
    @operator
    def rotation_courts():
        rotation.configure_courts(request.get_json())
        return jsonify(ok=True)

    @app.post("/api/rotation/checkin")
    @operator
    def rotation_checkin():
        return jsonify(rotation.checkin(request.get_json().get("player_ids", [])))

    @app.post("/api/rotation/groups/<int:group_id>")
    @operator
    def rotation_groups(group_id):
        values = request.get_json()
        if values.get("action") == "break" and values.get("confirmed") is not True:
            raise ValueError("Confirm breaking the temporary team")
        rotation.group_action(group_id, values.get("action"), values.get("partner_id"))
        return jsonify(ok=True)

    @app.post("/api/rotation/run")
    @operator
    def rotation_run():
        # Reserve first, then dispatch explicitly from the reservation cards.
        return jsonify(matches=rotation.generate(force=True))

    @app.post("/api/rotation/manual")
    @operator
    def rotation_manual():
        match = rotation.manual(request.get_json())
        return jsonify(match=match, delivery=state.start_match(match["id"]))

    @app.post("/api/rotation/matches/<int:match_id>")
    @operator
    def rotation_reservation(match_id):
        values = request.get_json()
        if values.get("action") == "start":
            return jsonify(state.start_match(match_id))
        if values.get("action") not in ("cancel", "reassign"):
            raise ValueError("Unknown reservation action")
        return jsonify(
            rotation.cancel(
                match_id,
                values.get("court_id") if values["action"] == "reassign" else None,
            )
        )
