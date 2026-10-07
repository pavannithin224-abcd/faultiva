"""Faultiva desktop shell.

Starts the dashboard server on a free loopback port and shows it in a native
window, so the installed app is the same tool as the local dashboard with no
browser chrome and no second UI to keep in step.

Binding is always 127.0.0.1.  The server can run Yosys and Verilator on behalf
of the user, so it must never be reachable from the network; the web dashboard
makes that an explicit choice, and here it is not a choice at all.

Falls back to the default browser when pywebview is unavailable, which keeps a
source checkout runnable without the GUI dependency.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "Faultiva"
APP_VERSION = "1.0.0"

# frozen by PyInstaller -> resources sit in the unpack dir; otherwise the repo
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_up(url: str, timeout: float = 45.0) -> bool:
    """Poll until the server answers, so the window never opens on a blank page."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _serve(host: str, port: int) -> None:
    """Run the dashboard server in this thread."""
    sys.path.insert(0, str(BUNDLE))
    sys.path.insert(0, str(BUNDLE / "app"))
    os.environ.setdefault("FAULTIVA_ROOT", str(BUNDLE))
    os.environ.setdefault("FAULTIVA_DESKTOP", "1")

    import uvicorn
    from app import app as application  # app/app.py defines `app`

    uvicorn.run(application, host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="faultiva",
                                     description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--port", type=int, default=0,
                        help="serve on this port instead of a free one")
    parser.add_argument("--browser", action="store_true",
                        help="open in the default browser instead of a window")
    parser.add_argument("--no-window", action="store_true",
                        help="serve only; open nothing")
    args = parser.parse_args(argv)

    host = "127.0.0.1"
    port = args.port or _free_port()
    url = f"http://{host}:{port}/"

    thread = threading.Thread(target=_serve, args=(host, port), daemon=True)
    thread.start()

    if not _wait_until_up(url):
        print(f"{APP_NAME}: the engine did not start", file=sys.stderr)
        return 1

    if args.no_window:
        print(f"{APP_NAME} serving on {url}  (Ctrl-C to stop)")
        try:
            while thread.is_alive():
                thread.join(1.0)
        except KeyboardInterrupt:
            pass
        return 0

    if not args.browser:
        try:
            import webview
        except ImportError:
            args.browser = True
        else:
            webview.create_window(f"{APP_NAME} {APP_VERSION}", url,
                                  width=1180, height=820,
                                  min_size=(900, 640),
                                  background_color="#0f1216")
            webview.start()
            return 0

    import webbrowser
    webbrowser.open(url)
    print(f"{APP_NAME} serving on {url}  (Ctrl-C to stop)")
    try:
        while thread.is_alive():
            thread.join(1.0)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
