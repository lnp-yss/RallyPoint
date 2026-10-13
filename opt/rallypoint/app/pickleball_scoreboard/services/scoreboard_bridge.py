"""Optional authenticated loopback IPC. No web/database imports on HDMI path."""

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from copy import deepcopy
from pathlib import Path
from queue import Queue, Empty
from threading import Thread, Lock, Event
from time import monotonic, time
from uuid import uuid4
import hmac
import json
import logging
import os
import sqlite3


@dataclass
class WebCommand:
    name: str
    payload: dict
    request_id: str
    timestamp: float = field(default_factory=monotonic)


class DurableOutbox:
    """Background-owned SQLite backlog; records survive a database outage/reboot."""

    def __init__(self, path):
        self.path = str(path)
        self.lock = Lock()
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def receipt(self, request_id, value=None):
        with self.lock, self.connect() as db:
            if value is not None:
                db.execute(
                    "INSERT OR REPLACE INTO receipts VALUES (?,?)",
                    (request_id, json.dumps(value)),
                )
                return value
            row = db.execute(
                "SELECT payload FROM receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def recover_receipts(self, applied):
        with self.lock, self.connect() as db:
            for request_id, encoded in db.execute(
                "SELECT request_id,payload FROM receipts"
            ).fetchall():
                receipt = json.loads(encoded)
                if receipt["status"] == "QUEUED":
                    receipt.update(
                        status="APPLIED" if request_id in applied else "REJECTED",
                        error=(
                            None
                            if request_id in applied
                            else "Interrupted before confirmation; check state and submit a fresh command"
                        ),
                    )
                    db.execute(
                        "UPDATE receipts SET payload=? WHERE request_id=?",
                        (json.dumps(receipt), request_id),
                    )

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def append(self, event):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO outbox(event_id,payload) VALUES (?,?)",
                (event["event_id"], json.dumps(event)),
            )

    def next(self):
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT id,payload FROM outbox ORDER BY id LIMIT 1"
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def remove(self, row_id):
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM outbox WHERE id=?", (row_id,))


class ScoreboardBridge:
    def __init__(
        self,
        root: Path,
        commands: Queue,
        token: str,
        port=8765,
        database_url=None,
        hub_url=None,
    ):
        if len(token) < 32:
            raise ValueError("Bridge requires a secret token of at least 32 characters")
        self.token, self.commands = token, commands
        self.lock = Lock()
        self.request_lock = Lock()
        self.snapshot = {"connected": True, "match_id": None, "revision": 0}
        self.sync_status = {"online": False, "pending": 0, "last_sync": None}
        self.receipts = {}
        self.events = Queue()
        self.stop = Event()
        self.root = Path(root)
        self.outbox = DurableOutbox(self.root / "data/web_outbox.sqlite3")
        try:
            saved = json.loads(
                (self.root / "data/current_match.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            saved = {}
        self.outbox.recover_receipts(
            saved.get("external_context", {}).get("applied_requests", [])
        )
        self.database_url = database_url
        self.hub_url = hub_url.rstrip("/") if hub_url else None
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, data, status=200):
                encoded = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def authenticated(self):
                return hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + bridge.token
                )

            def do_GET(self):
                if not self.authenticated():
                    return self.send_json({"error": "Unauthorized"}, 401)
                if self.path == "/state":
                    with bridge.lock:
                        data = deepcopy(bridge.snapshot)
                        data["sync"] = dict(bridge.sync_status)
                        data["sync"]["pending"] += bridge.events.qsize()
                else:
                    request_id = self.path.removeprefix("/receipts/")
                    with bridge.lock:
                        data = deepcopy(bridge.receipts.get(request_id))
                    if data is None:
                        data = bridge.outbox.receipt(request_id)
                self.send_json(data or {"error": "Not found"}, 200 if data else 404)

            def do_POST(self):
                if not self.authenticated() or self.path != "/commands":
                    return self.send_json({"error": "Unauthorized"}, 401)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 32768:
                        raise ValueError("Invalid request size")
                    data = json.loads(self.rfile.read(length))
                    request_id = str(data["request_id"])
                    from uuid import UUID

                    UUID(request_id)
                    if data["name"] not in bridge.allowed_commands:
                        raise ValueError("Unsupported command")
                    with bridge.request_lock:
                        receipt = bridge.outbox.receipt(request_id)
                        if receipt:
                            return self.send_json(receipt, 202)
                        receipt = {"request_id": request_id, "status": "QUEUED"}
                        bridge.receipts[request_id] = receipt
                        bridge.outbox.receipt(request_id, receipt)
                    bridge.commands.put(
                        WebCommand(data["name"], data.get("payload", {}), request_id)
                    )
                    self.send_json(receipt, 202)
                except (ValueError, KeyError, TypeError):
                    self.send_json({"error": "Invalid command"}, 400)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.http_thread = Thread(
            target=self.server.serve_forever, daemon=True, name="scoreboard-ipc"
        )
        self.worker = Thread(
            target=self.flush_worker, daemon=True, name="match-event-writer"
        )
        self.http_thread.start()
        self.worker.start()

    allowed_commands = {
        "CHANGE_FIRST_SERVE",
        "CORRECT_RESULT",
        "SET_SCORES",
        "DECLARE_WINNER",
        "START_MATCH",
        "TEAM_A_POINT",
        "TEAM_B_POINT",
        "TEAM_A_SERVICE",
        "TEAM_B_SERVICE",
        "TEAM_A_UNDO",
        "TEAM_B_UNDO",
        "TEAM_A_TIMEOUT",
        "TEAM_B_TIMEOUT",
        "CHANGE_SERVER",
        "NEXT_GAME",
        "END_GAME",
        "PAUSE",
        "RESUME",
        "END_MATCH",
        "RESET",
        "RESUME_RECOVERY",
    }

    def complete(self, command, error=None):
        with self.lock:
            self.receipts[command.request_id] = {
                "request_id": command.request_id,
                "status": "REJECTED" if error else "APPLIED",
                "error": error,
            }
            if len(self.receipts) > 2000:
                self.receipts.pop(next(iter(self.receipts)))
            # Persistence is performed by the worker, never on the Pygame thread.
            self.events.put(
                {
                    "receipt_id": command.request_id,
                    "receipt": self.receipts[command.request_id],
                }
            )

    def publish(self, snapshot: dict, event_type: str, match_id=None, device_id=None):
        data = deepcopy(snapshot)
        with self.lock:
            data.update(
                match_id=match_id,
                connected=True,
                revision=self.snapshot["revision"] + 1,
                updated_at=time(),
            )
            self.snapshot = data
        if match_id or device_id:
            self.events.put(
                {
                    "event_id": str(uuid4()),
                    "event_type": event_type,
                    "match_id": match_id,
                    "device_id": device_id,
                    "snapshot": data,
                    "occurred_at": time(),
                }
            )

    def flush_worker(self):
        service = None
        retry_at = 0.0
        last_branding = None
        while not self.stop.is_set() or not self.events.empty():
            try:
                # Queue insertion wakes immediately for scores. Idle polling
                # only services an existing durable backlog or DB retry.
                event = self.events.get(timeout=0.25)
                if "receipt_id" in event:
                    self.outbox.receipt(event["receipt_id"], event["receipt"])
                else:
                    self.outbox.append(event)
            except Empty:
                pass
            except Exception:
                logging.exception("Local event backlog write failed")
                if "event" in locals():
                    self.events.put(event)
                self.stop.wait(0.5)
                continue
            if not (self.database_url or self.hub_url) or monotonic() < retry_at:
                continue
            try:
                if service is None and not self.hub_url:
                    from ..web.database import Database
                    from ..web.club_service import ClubService

                    service = ClubService(Database(self.database_url))
                row = self.outbox.next()
                if row:
                    if self.hub_url:
                        from urllib.request import Request, urlopen

                        req = Request(
                            self.hub_url + "/api/device-events",
                            data=json.dumps(row[1]).encode(),
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": "Bearer " + self.token,
                            },
                        )
                        with urlopen(req, timeout=2) as response:
                            acknowledgement = json.load(response)
                            if not acknowledgement.get("accepted"):
                                raise ValueError("Hub did not acknowledge event")
                            roster = acknowledgement.get("roster")
                            reported = row[1].get("snapshot", {})
                            if (isinstance(roster, dict)
                                    and roster.get("match_id") == row[1].get("match_id")
                                    and any(reported.get("settings", {}).get(k) != v
                                            for k, v in roster.get("settings", {}).items())):
                                self.commands.put(WebCommand("UPDATE_ROSTER", roster, str(uuid4())))
                            branding = acknowledgement.get("branding")
                            if isinstance(branding, dict) and branding != last_branding:
                                self.commands.put(WebCommand("UPDATE_BRANDING", branding, str(uuid4())))
                                last_branding = branding
                    else:
                        service.publish_state(row[1])
                    self.outbox.remove(row[0])
                with self.outbox.connect() as backlog:
                    pending = backlog.execute("SELECT COUNT(*) FROM outbox").fetchone()[
                        0
                    ]
                self.sync_status = {
                    "online": True,
                    "pending": pending,
                    "last_sync": time(),
                }
            except Exception:
                with self.outbox.connect() as backlog:
                    pending = backlog.execute("SELECT COUNT(*) FROM outbox").fetchone()[
                        0
                    ]
                self.sync_status = {
                    **self.sync_status,
                    "online": False,
                    "pending": pending,
                }
                logging.warning(
                    "Club database unavailable; scoring continues; event backlog retained",
                    exc_info=True,
                )
                retry_at = monotonic() + 2

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.stop.set()
        self.worker.join(timeout=5)


def optional_bridge(root, queue):
    if os.environ.get("RALLYPOINT_BRIDGE", "0") != "1":
        return None
    try:
        return ScoreboardBridge(
            root,
            queue,
            os.environ.get("RALLYPOINT_BRIDGE_TOKEN", ""),
            int(os.environ.get("RALLYPOINT_BRIDGE_PORT", "8765")),
            os.environ.get("RALLYPOINT_DATABASE_URL"),
            os.environ.get("RALLYPOINT_HUB_URL"),
        )
    except Exception:
        logging.exception("Web bridge unavailable; HDMI scoring remains operational")
        return None
