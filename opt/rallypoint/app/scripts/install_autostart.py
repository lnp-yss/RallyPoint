"""Install a desktop-session hook and rendered systemd user service on Linux."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess


def main():
    if os.name != "posix" or os.geteuid() == 0:
        raise SystemExit("Run on Linux as the desktop user, without sudo")
    root = Path(__file__).resolve().parent.parent
    # systemd specifier characters must be literal; quotes/backslashes/newlines are unsupported.
    if any(c in str(root) for c in '\n\r"\\'):
        raise SystemExit(
            "Use a project path without quotes, backslashes or newlines"
        )
    config = Path.home() / ".config"
    service_dir = config / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    template = (root / "pickleball-scoreboard.service").read_text()
    (service_dir / "pickleball-scoreboard.service").write_text(
        template.replace("@PROJECT_DIR@", str(root).replace("%", "%%"))
    )
    command = shlex.quote(str(root / "desktop_start.sh"))
    if shutil.which("labwc"):
        path = config / "labwc" / "autostart"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            text = path.read_text()
            shutil.copy2(path, path.with_suffix(".scoreboard-backup"))
        else:
            text = "#!/bin/sh\n"
        if "# Rally Point scoreboard" not in text:
            path.write_text(
                text + "\n# Rally Point scoreboard\n" + command + " &\n"
            )
    else:
        path = config / "autostart" / "pickleball-scoreboard.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        desktop_command = (
            str(root / "desktop_start.sh")
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        path.write_text(
            '[Desktop Entry]\nType=Application\nName=Rally Point Scoreboard\nExec="'
            + desktop_command
            + '"\nTerminal=false\n'
        )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    print(
        "Installed desktop autostart. Enable desktop autologin and reboot to verify."
    )


if __name__ == "__main__":
    main()
