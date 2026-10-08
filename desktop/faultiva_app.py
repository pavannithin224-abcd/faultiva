"""Faultiva desktop shell.

Starts the dashboard server on a free loopback port and shows it in a native
window, so the installed app is the same tool as the local dashboard with no
browser chrome and no second UI to keep in step.

Binding is always 127.0.0.1.  The server can run Yosys and Verilator on behalf
of the user, so it must never be reachable from the network; the web dashboard
makes that an explicit choice, and here it is not a choice at all.

Two things matter when this runs as an installed, windowed application:

    sys.stdout and sys.stderr are None.  PyInstaller's windowed mode gives the
    process no console, and uvicorn reads sys.stdout.isatty() while configuring
    its logger - so the server thread dies before serving anything and the
    process disappears a few seconds after launch with nothing on screen.  Both
    streams are repaired before the server is touched.

    A desktop app that exits silently is undebuggable.  Every failure is
    written to a log file and shown in a message box.

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
import traceback
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "Faultiva"
APP_VERSION = "1.0.0"

# frozen by PyInstaller -> resources sit in the unpack dir; otherwise the repo
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
FROZEN = hasattr(sys, "_MEIPASS")


def _log_path() -> Path:
    """Where to write diagnostics, in a directory that always exists."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or "."
    directory = Path(base) / "Faultiva"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "faultiva.log"
    except OSError:
        import tempfile
        return Path(tempfile.gettempdir()) / "faultiva.log"


LOG = _log_path()


def _attach_streams() -> bool:
    """Give the process real stdout/stderr.

    Windowed PyInstaller builds set both to None.  uvicorn calls
    sys.stdout.isatty() while configuring its logger, so without this the app
    dies on startup with no message anywhere.

    Returns True when a repair was needed, which also means the log file is now
    the destination for ordinary printing.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        handle = open(LOG, "a", encoding="utf-8", buffering=1)
    except OSError:
        handle = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle
    sys.__stdout__ = sys.stdout
    sys.__stderr__ = sys.stderr
    return True


_STREAMS_REPAIRED = _attach_streams()

# The V1 verifier was pickled under scikit-learn 1.7.2 and loads here under a
# newer release.  Output was checked to be byte-identical across both, so the
# version warning is noise in a GUI log; keep it out of the user's log file.
if _STREAMS_REPAIRED:
    import warnings
    warnings.filterwarnings("ignore", message=".*InconsistentVersionWarning.*")
    try:
        from sklearn.exceptions import InconsistentVersionWarning
        warnings.simplefilter("ignore", InconsistentVersionWarning)
    except Exception:
        pass


def _note(message: str) -> None:
    """Timestamped line in the log; harmless if the log cannot be written.

    When the streams were repaired they point at this same file, so printing as
    well would write everything twice.  Print only when stdout is somewhere
    else, i.e. a console build or a source checkout.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        pass
    if _STREAMS_REPAIRED:
        return
    try:
        print(f"{stamp}  {message}")
    except (ValueError, OSError):
        pass


def _fatal(title: str, detail: str) -> None:
    """Record a startup failure and, when windowed, say so on screen."""
    _note(f"FATAL {title}\n{detail}")
    if not FROZEN or os.environ.get("FAULTIVA_NO_DIALOG"):
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{detail}\n\nDetails were written to:\n{LOG}",
            f"{APP_NAME} - {title}",
            0x10,
        )
    except Exception:
        pass


def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_up(url: str, timeout: float = 90.0) -> bool:
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


_SERVER_ERROR: list[str] = []


def _serve(host: str, port: int) -> None:
    """Run the dashboard server in this thread."""
    try:
        sys.path.insert(0, str(BUNDLE))
        sys.path.insert(0, str(BUNDLE / "app"))
        os.environ.setdefault("FAULTIVA_ROOT", str(BUNDLE))
        os.environ.setdefault("FAULTIVA_DESKTOP", "1")

        import uvicorn
        from app import app as application  # app/app.py defines `app`

        _note(f"serving on http://{host}:{port}/ from {BUNDLE}")
        uvicorn.run(application, host=host, port=port,
                    log_level="warning", access_log=False)
    except BaseException:
        _SERVER_ERROR.append(traceback.format_exc())
        _note("server thread failed\n" + _SERVER_ERROR[-1])


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

    _note(f"--- {APP_NAME} {APP_VERSION} starting (frozen={FROZEN}) ---")

    host = "127.0.0.1"
    port = args.port or _free_port()
    url = f"http://{host}:{port}/"

    thread = threading.Thread(target=_serve, args=(host, port), daemon=True)
    thread.start()

    if not _wait_until_up(url):
        detail = (_SERVER_ERROR[0] if _SERVER_ERROR
                  else "The engine did not respond within 90 seconds.")
        _fatal("could not start", detail)
        return 1

    _note("engine ready")

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
        except ImportError as exc:
            _note(f"pywebview unavailable ({exc}); using the browser")
            args.browser = True
        else:
            try:
                webview.create_window(f"{APP_NAME} {APP_VERSION}", url,
                                      width=1180, height=820,
                                      min_size=(900, 640),
                                      background_color="#0f1216")
                webview.start()
                _note("window closed")
                return 0
            except Exception:
                _note("window failed, falling back to the browser\n"
                      + traceback.format_exc())
                args.browser = True

    import webbrowser
    webbrowser.open(url)
    _note(f"opened {url} in the default browser")
    print(f"{APP_NAME} serving on {url}  (Ctrl-C to stop)")
    try:
        while thread.is_alive():
            thread.join(1.0)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        _fatal("unexpected error", traceback.format_exc())
        raise SystemExit(1)
