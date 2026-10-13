from test_web_club import web, signed_in
import pytest


def test_operator_settings_and_viewer_denied(web):
    client, csrf = signed_in(web, "OPERATOR")
    headers = {"X-CSRF-Token": csrf}
    for path in ("/settings/courts", "/settings/matchmaking", "/api/rotation"):
        assert client.get(path).status_code == 200
    assert (
        client.post(
            "/api/rotation/courts", json={"count": 5}, headers=headers
        ).status_code
        == 200
    )
    assert len(client.get("/api/rotation").json["courts"]) == 5
    assert (
        client.post(
            "/api/rotation/settings",
            json={"name": "Friday Fun", "settings": {"mode": "RANDOM"}},
            headers=headers,
        ).status_code
        == 200
    )
    assert client.get("/api/rotation").json["settings"]["mode"] == "RANDOM"
    assert client.post("/api/rotation/settings", json={}).status_code == 403
    viewer, token = signed_in(web, "VIEWER")
    assert viewer.get("/api/rotation").status_code == 403
    assert (
        viewer.post(
            "/api/rotation/run", json={}, headers={"X-CSRF-Token": token}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "BAD"},
        {"waiting_weight": "35"},
        {"waiting_weight": True},
        {"grace_period_seconds": float("inf")},
        {"candidate_limit": 2},
        {"maximum_consecutive_wins": 0},
        {"long_wait_minutes": 25},
        {"automatic_assignment": "yes"},
        {"default_rating": 9},
    ],
)
def test_invalid_configuration_rejected(web, values):
    client, token = signed_in(web, "OPERATOR")
    response = client.post(
        "/api/rotation/settings",
        json={"settings": values},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 400


def test_group_break_requires_confirmation(web):
    club = web.extensions["club"]
    ids = [club.save_player({"display_name": name})["id"] for name in ("A", "B")]
    first = club.rotation.checkin([ids[0]])
    second = club.rotation.checkin([ids[1]])
    club.rotation.group_action(first["id"], "pair", second["id"])
    client, token = signed_in(web, "OPERATOR")
    path = "/api/rotation/groups/" + str(first["id"])
    assert (
        client.post(
            path, json={"action": "break"}, headers={"X-CSRF-Token": token}
        ).status_code
        == 400
    )
    assert (
        client.post(
            path,
            json={"action": "break", "confirmed": True},
            headers={"X-CSRF-Token": token},
        ).status_code
        == 200
    )
