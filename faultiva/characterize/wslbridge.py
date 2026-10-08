"""Run the build tools inside WSL when that is where they live.

Characterization is a Linux step: Verilator emits C++ and invokes a compiler.
On Windows the tools usually exist only inside a WSL distribution, and
toolchain detection reports exactly that - but executing `/usr/bin/yosys` as a
Windows program fails with "[WinError 2] The system cannot find the file
specified".  This module turns such a command into

    wsl.exe -d <distro> -- <command, with Windows paths translated>

Set FAULTIVA_NO_WSL=1 to disable the bridge, or FAULTIVA_WSL_DISTRO to choose
a distribution other than the default.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess

WINDOWS = platform.system() == "Windows"
_NO_WINDOW = 0x08000000 if WINDOWS else 0

# C:\dir\file or C:/dir/file, anywhere inside a string
_WIN_PATH = re.compile(r"\b([A-Za-z]):[\\/]([^\s\"\';|<>]*)")


def to_wsl_path(text: str) -> str:
    """Rewrite every Windows path inside `text` as the path WSL sees."""
    def one(m: "re.Match[str]") -> str:
        drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
        return "/mnt/" + drive + "/" + rest
    return _WIN_PATH.sub(one, text)


class Bridge:
    """How to invoke a tool: directly, or through a WSL distribution."""

    __slots__ = ("distro",)

    def __init__(self, distro: str | None = None) -> None:
        self.distro = distro

    @property
    def active(self) -> bool:
        return bool(self.distro)

    def command(self, argv: list[str]) -> list[str]:
        """The argv to actually hand to subprocess."""
        if not self.active:
            return [str(a) for a in argv]
        return ["wsl.exe", "-d", str(self.distro), "--"] + [
            to_wsl_path(str(a)) for a in argv]

    def __repr__(self) -> str:
        return ("Bridge(distro=%r)" % self.distro) if self.active \
            else "Bridge(direct)"


DIRECT = Bridge(None)


def detect(yosys: str | None = None, verilator: str | None = None) -> Bridge:
    """Decide whether tool invocations have to cross into WSL.

    A tool path that is absolute-POSIX while we are running on Windows can
    only be inside a distribution, so that is the signal.
    """
    if not WINDOWS or os.environ.get("FAULTIVA_NO_WSL") == "1":
        return DIRECT

    posix = any(p and p.startswith("/") and not re.match(r"^[A-Za-z]:", p)
                for p in (yosys, verilator))
    if not posix:
        return DIRECT

    stated = os.environ.get("FAULTIVA_WSL_DISTRO")
    if stated:
        return Bridge(stated)

    try:
        proc = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True,
                              timeout=25, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return DIRECT
    if proc.returncode != 0:
        return DIRECT

    # wsl -l -q is UTF-16 on most builds
    raw = proc.stdout
    for enc in ("utf-16-le", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return DIRECT

    names = [n.strip() for n in text.replace("\x00", "").splitlines() if n.strip()]
    return Bridge(names[0]) if names else DIRECT


def check(bridge: Bridge, yosys: str, verilator: str) -> str | None:
    """Confirm both tools really run through the bridge.

    Returns None when they do, otherwise a message naming what failed - better
    to say so before a campaign starts than to fail at phase four.
    """
    for name, exe, flag in (("yosys", yosys, "-V"),
                            ("verilator", verilator, "--version")):
        try:
            proc = subprocess.run(bridge.command([exe, flag]),
                                  capture_output=True, text=True, timeout=90)
        except OSError as exc:
            return "%s could not be started (%s)" % (name, type(exc).__name__)
        except subprocess.TimeoutExpired:
            return "%s did not respond" % name
        if proc.returncode != 0:
            first = (proc.stderr or proc.stdout or "").strip().splitlines()
            return "%s exited %d%s" % (
                name, proc.returncode, (": " + first[0][:120]) if first else "")
    return None
