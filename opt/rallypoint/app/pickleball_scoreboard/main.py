"""Launch with python -m pickleball_scoreboard.main."""

import argparse
import logging
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Rally Point Pickleball Scoreboard"
    )
    parser.add_argument(
        "--keyboard", action="store_true", help="Disable GPIO detection"
    )
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--smoke-frames", type=int, help="Exit after N frames for verification"
    )
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    from .app import Application

    app = None
    try:
        app = Application(args.data_root, args.keyboard, args.windowed)
        app.run(args.smoke_frames, args.screenshot)
    except KeyboardInterrupt:
        if app:
            app.close()


if __name__ == "__main__":
    main()
