"""Host-only scheduler. Reservations commit before any network communication."""

import logging
import signal
from threading import Event
from sqlalchemy import select
from .shared_system import SharedSystem
from .models import Match


def tick(app):
    rotation = app.extensions["rotation"]
    created = rotation.generate()
    state = app.extensions["match_state"]
    with rotation.db.transaction() as session:
        pending = [
            m.id
            for m in session.scalars(
                select(Match).where(Match.status == "RESERVED").order_by(Match.id)
            )
            if (m.matchmaking_json or {}).get("dispatch_automatically")
        ]
    # A failed/uncertain STARTING delivery stays reserved for operator review.
    for match_id in pending:
        try:
            state.start_match(match_id)
        except (OSError, ValueError):
            logging.exception("Could not dispatch reserved match %s", match_id)
    return created


def main():
    logging.basicConfig(level=logging.INFO)
    host = SharedSystem()
    app = host.root_app
    stopped = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            try:
                for workspace in [app] + [
                    host.workspace(r["slug"]) for r in host.records()
                ]:
                    try:
                        tick(workspace)
                    except Exception:
                        logging.exception("Club matchmaking run failed")
            except Exception:
                logging.exception("Matchmaking run failed; reservations rolled back")
            stopped.wait(5)
    finally:
        app.extensions["database"].engine.dispose()


if __name__ == "__main__":
    main()
