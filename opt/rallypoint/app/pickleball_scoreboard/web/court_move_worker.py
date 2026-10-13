"""Durable serialized court moves. Runs as rallypoint, outside web request threads."""

import json, logging, signal, subprocess, time
from uuid import uuid4
from pathlib import Path
from threading import Event
from sqlalchemy import select
from .shared_system import SharedSystem
from .court_transfers import application, binding, idle
from .models import Court, ScoreboardDevice, Match, utcnow


def rpc(inventory, physical_id, payload):
    device = inventory[str(physical_id)]
    if device.get("local"):
        command = ["sudo", "-n", "/usr/local/sbin/rallypoint-court-agent"]
    else:
        command = [
            "ssh",
            "-i",
            "/etc/rallypoint/court-manager/id_ed25519",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "CheckHostIP=no",
            "-o",
            "UserKnownHostsFile=/etc/rallypoint/court-manager/known_hosts",
            *(["-o", "HostKeyAlias=" + device["host_key_alias"]] if device.get("host_key_alias") else []),
            device["ssh"],
        ]
    try:
        result = subprocess.run(
            command, input=json.dumps(payload), text=True, capture_output=True, timeout=50
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("Device did not respond within 50 seconds. Check its connection before retrying.") from exc
    try:
        reply = json.loads(result.stdout)
    except ValueError:
        error = result.stderr.lower()
        if "host key verification failed" in error or "remote host identification has changed" in error:
            message = "Trusted SSH device key did not match. Verify this is the paired Pi; do not disable key verification."
        elif "permission denied" in error:
            message = "SSH authentication failed. The paired court management key was rejected."
        elif "could not resolve hostname" in error or "name or service not known" in error:
            message = "Hostname could not be resolved. Try the Pi's current IP shown by the router."
        elif "connection refused" in error:
            message = "SSH connection refused. Check that SSH is enabled on this Pi."
        elif "timed out" in error or "no route to host" in error or "network is unreachable" in error:
            message = "Device is unreachable. Check its current DHCP address and router client isolation."
        else:
            message = "No valid response from the paired device agent. Check power, address, SSH and court manager service."
        raise OSError(message)
    if not reply.get("ok"):
        raise ValueError(reply.get("error", "Device rejected configuration"))
    return reply


class MoveWorker:
    def __init__(self, host, transport):
        self.host, self.transport = host, transport

    def update(self, job, status, message, payload=None):
        with self.host.connect() as db:
            db.execute(
                "UPDATE court_moves SET status=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, message, job),
            )
            if payload is not None:
                db.execute(
                    "UPDATE court_moves SET payload=? WHERE id=?",
                    (json.dumps(payload), job),
                )

    def finish_source(self, job, payload, source):
        """End only the reviewed source match, then wait for durable projection."""
        app = application(self.host, source["slug"])
        state = app.extensions["match_state"]
        snapshot = state.request("/state", url=source["bridge_url"])
        if not snapshot.get("connected"):
            raise ValueError("Source court is offline; no match was ended")
        if snapshot.get("match_id") != payload.get("expected_match_id"):
            raise ValueError("Source has a different match; review the move again")
        with app.extensions["database"].transaction() as s:
            active = list(s.scalars(select(Match.id).where(
                Match.court_id == source["court_id"], Match.status.in_(("ACTIVE", "STARTING", "RESERVED")))))
        if not set(active).issubset(set(payload.get("source_matches", []))):
            raise ValueError("Source has a new reservation; review the move again")
        busy = snapshot.get("match_exists", bool(snapshot.get("match_id"))) and snapshot.get("state", {}).get("status") not in ("complete", "ended")
        if busy:
            payload.setdefault("ending_request_id", str(uuid4()))
            self.update(job, "MOVING", "Host override: ending the source match", payload)
            receipt = state.request("/commands", {
                "name": "END_MATCH", "request_id": payload["ending_request_id"],
                "payload": {"match_id": snapshot.get("match_id"), "host_override": True,
                            "actor": {"username": payload["actor"]}}}, source["bridge_url"])
            deadline = time.monotonic() + 30
            while True:
                receipt = state.request("/receipts/" + receipt["request_id"], url=source["bridge_url"])
                if receipt.get("status") == "REJECTED":
                    raise ValueError(receipt.get("error") or "HDMI rejected ending the match")
                if receipt.get("status") == "APPLIED":
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("End-match confirmation pending; transfer stopped")
                time.sleep(.25)
        self.update(job, "MOVING", "Waiting for the ended match and scores to synchronize", payload)
        deadline = time.monotonic() + 30
        while True:
            snapshot = state.request("/state", url=source["bridge_url"])
            if snapshot.get("match_id") != payload.get("expected_match_id"):
                raise ValueError("Source has a different match; transfer stopped")
            try:
                idle(snapshot)
                if snapshot.get("sync", {}).get("online") is False:
                    raise ValueError("Source scores are not synchronized")
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise ValueError("Source match did not finish synchronizing; transfer stopped")
                time.sleep(.25)
        # A reservation which never reached HDMI can be released without a score.
        with app.extensions["database"].transaction() as s:
            reservations = [(m.id, m.status) for m in s.scalars(select(Match).where(
                Match.court_id == source["court_id"], Match.status.in_(("RESERVED", "STARTING"))))]
        for match_id, status in reservations:
            if match_id != snapshot.get("match_id"):
                app.extensions["club"].cancel_reservation(match_id)
        with app.extensions["database"].transaction() as s:
            if s.scalar(select(Match.id).where(Match.court_id == source["court_id"],
                       Match.status.in_(("ACTIVE", "STARTING", "RESERVED")))):
                raise ValueError("Source still has an active match record; transfer stopped")

    def freeze(self, job, payload):
        source = binding(self.host, payload["physical_id"])
        # Repeat busy/identity checks, ignoring this job's own queue entry.
        app = application(self.host, source["slug"])
        target = application(self.host, payload["target_slug"])
        if source["revision"] != payload["revision"]:
            raise ValueError("Assignment changed after confirmation")
        snapshot = app.extensions["match_state"].request(
            "/state", url=source["bridge_url"]
        )
        if not payload.get("end_match"):
            idle(snapshot)
        with target.extensions["database"].assignment_transaction() as s:
            court = s.scalar(
                select(Court).where(Court.number == payload["court_number"])
            )
            if court and (
                court.active_match_id
                or s.scalar(
                    select(Match.id).where(
                        Match.court_id == court.id,
                        Match.status.in_(("ACTIVE", "STARTING", "RESERVED")),
                    )
                )
            ):
                raise ValueError("Destination has an active or reserved match")
            old_target = (
                None
                if court is None
                else {"enabled": court.enabled, "status": court.status}
            )
            if court is None:
                court = Court(
                    name="Court " + str(payload["court_number"]),
                    number=payload["court_number"],
                )
                s.add(court)
                s.flush()
            device = s.scalar(
                select(ScoreboardDevice).where(ScoreboardDevice.court_id == court.id)
            )
            if device and device.bridge_url != source["bridge_url"]:
                raise ValueError("Destination is assigned to another device")
            old_device = (
                None
                if device is None
                else {
                    k: getattr(device, k)
                    for k in ("bridge_url", "hostname", "ip_address", "status")
                }
            )
            if device is None:
                name = source["name"]
                if s.scalar(
                    select(ScoreboardDevice.id).where(
                        ScoreboardDevice.device_name == name
                    )
                ):
                    name += " · " + str(court.number)
                device = ScoreboardDevice(
                    device_name=name, court_id=court.id, bridge_url=source["bridge_url"]
                )
                s.add(device)
                s.flush()
            device.hostname = snapshot.get("identity", {}).get("hostname", "")
            context = {
                "source": source,
                "target_court_id": court.id,
                "target_device_id": device.id,
                "old_target": old_target,
                "old_device": old_device,
                "court_name": court.name,
            }
            payload["context"] = context
            self.update(job, "MOVING", "Preparing the destination court", payload)
            court.enabled = 0
            court.status = "MOVING"
        if payload.get("end_match"):
            self.finish_source(job, payload, source)
        with app.extensions["database"].assignment_transaction() as s:
            court = s.get(Court, source["court_id"])
            if court.active_match_id or s.scalar(
                select(Match.id).where(
                    Match.court_id == court.id,
                    Match.status.in_(("ACTIVE", "STARTING", "RESERVED")),
                )
            ):
                raise ValueError("Source court became occupied; move cancelled")
            context["old_source"] = {"enabled": court.enabled, "status": court.status}
            self.update(
                job, "MOVING", "Pausing setup while the court switches", payload
            )
            court.enabled = 0
            court.status = "MOVING"
        return context

    def restore(self, payload):
        context = payload["context"]
        source = context["source"]
        with application(self.host, source["slug"]).extensions[
            "database"
        ].assignment_transaction() as s:
            court = s.get(Court, source["court_id"])
            for k, v in context.get("old_source", {}).items():
                if k != "status" or not court.active_match_id:
                    setattr(court, k, v)
        with application(self.host, payload["target_slug"]).extensions[
            "database"
        ].assignment_transaction() as s:
            court = s.get(Court, context["target_court_id"])
            device = s.get(ScoreboardDevice, context["target_device_id"])
            if court:
                for k, v in (
                    context["old_target"] or {"enabled": 0, "status": "DISABLED"}
                ).items():
                    setattr(court, k, v)
            if device:
                for k, v in (context["old_device"] or {"status": "OFFLINE"}).items():
                    setattr(device, k, v)

    def perform(self, job, payload):
        context = payload.get("context")
        attempted = False
        try:
            if context is None:
                context = self.freeze(job, payload)
            source = context["source"]
            if "old_source" not in context:
                if payload.get("end_match"):
                    self.finish_source(job, payload, source)
                with application(self.host, source["slug"]).extensions["database"].assignment_transaction() as session:
                    court = session.get(Court, source["court_id"])
                    if court.active_match_id or session.scalar(select(Match.id).where(Match.court_id == court.id, Match.status.in_(("ACTIVE", "STARTING", "RESERVED")))):
                        raise ValueError("Source court became occupied; move cancelled")
                    context["old_source"] = {"enabled": court.enabled, "status": court.status}
                    self.update(job, "MOVING", "Recovering the court preparation", payload)
                    court.enabled = 0
                    court.status = "MOVING"
            target = application(self.host, payload["target_slug"])
            self.update(job, "MOVING", "Applying the club settings to the Pi")
            attempted = True
            reply = self.transport(
                source["id"],
                {
                    "action": "apply",
                    "job_id": job,
                    "profile": payload["target_slug"],
                    "device_id": context["target_device_id"],
                    "court_id": context["target_court_id"],
                    "court_number": payload["court_number"],
                    "court_name": context["court_name"],
                    "club_name": target.config.get(
                        "CLUB_NAME", "RALLY POINT PICKLEBALL CLUB"
                    ),
                    "hub_url": __import__("pickleball_scoreboard.web.network_settings", fromlist=["hub_url"]).hub_url()
                    + (
                        ""
                        if payload["target_slug"] == "legacy"
                        else "/clubs/" + payload["target_slug"]
                    ),
                    "token": target.config["BRIDGE_TOKEN"],
                },
            )
            identity = reply.get("identity", {})
            if (
                not reply.get("connected")
                or int(identity.get("device_id", 0)) != context["target_device_id"]
                or int(identity.get("court_id", 0)) != context["target_court_id"]
            ):
                raise ValueError("The Pi returned an unexpected court identity")
            self.update(job, "MOVING", "Verifying the new connection")
            deadline = time.monotonic() + 15
            while True:
                live = target.extensions["match_state"].request(
                    "/state", url=source["bridge_url"]
                )
                if (
                    live.get("connected")
                    and int(live.get("identity", {}).get("court_id", 0))
                    == context["target_court_id"]
                    and live.get("sync", {}).get("online")
                    and live.get("sync", {}).get("pending", 0) == 0
                ):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("Scoreboard synchronization did not recover")
                time.sleep(0.25)
            # Disabling a court never deletes historical players, matches or scores.
            with application(self.host, source["slug"]).extensions[
                "database"
            ].assignment_transaction() as s:
                s.get(Court, source["court_id"]).enabled = 0
                s.get(Court, source["court_id"]).status = "DISABLED"
                s.get(ScoreboardDevice, source["device_id"]).status = "OFFLINE"
            self.transport(source["id"], {"action": "finalize", "job_id": job})
            with target.extensions["database"].assignment_transaction() as s:
                court = s.get(Court, context["target_court_id"])
                court.enabled = 1
                if not court.active_match_id:
                    court.status = "AVAILABLE"
                    court.available_since = utcnow()
                device = s.get(ScoreboardDevice, context["target_device_id"])
                device.bridge_url = source["bridge_url"]
                device.hostname = identity.get("hostname", "")
                device.status = "ONLINE"
                device.last_seen_at = utcnow()
            with self.host.connect() as db:
                db.execute(
                    "UPDATE physical_courts SET slug=?,court_id=?,device_id=?,revision=revision+1 WHERE id=?",
                    (
                        payload["target_slug"],
                        context["target_court_id"],
                        context["target_device_id"],
                        source["id"],
                    ),
                )
                db.execute(
                    "UPDATE court_moves SET status='DONE',message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    ("Court is online in " + payload["target_name"], job),
                )
        except Exception as exc:
            logging.exception("Court move %s failed", job)
            try:
                if attempted:
                    self.transport(
                        payload["physical_id"], {"action": "rollback", "job_id": job}
                    )
                if payload.get("context"):
                    self.restore(payload)
                self.update(
                    job, "FAILED", str(exc) + " Previous club assignment restored."
                    + (" Any match already ended remains ended." if payload.get("ending_request_id") else "")
                )
            except Exception:
                logging.exception("Court rollback requires review: %s", job)
                self.update(
                    job, "NEEDS_REVIEW", "Connection recovery is required. " + str(exc)
                )

    def once(self):
        with self.host.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM court_moves WHERE status IN ('MOVING','QUEUED') ORDER BY created_at,rowid LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE court_moves SET status='MOVING' WHERE id=?", (row["id"],)
                )
        if row:
            self.perform(row["id"], json.loads(row["payload"]))
        return bool(row)


def main():
    logging.basicConfig(level=logging.INFO)
    host = SharedSystem()
    import fcntl

    worker_lock = (host.directory.parent / "court-manager.lock").open("w")
    fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from .network_settings import once as network_once, initialize
    initialize(host)
    worker = MoveWorker(
        host, lambda physical, payload: rpc(json.loads(Path("/etc/rallypoint/court-manager/inventory.json").read_text()), physical, payload)
    )
    stopped = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            if not worker.once():
                network_once(host, rpc)
        except Exception:
            logging.exception("Court manager is waiting for database recovery")
        stopped.wait(0.5)


if __name__ == "__main__":
    main()
