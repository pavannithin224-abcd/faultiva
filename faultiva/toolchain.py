"""Toolchain discovery for characterization.

Diagnosis needs nothing but Python and numpy.  Characterizing a new circuit
needs Yosys to synthesize it and Verilator to simulate it, and neither is
bundled, so the app has to find out at runtime whether this machine can do it
and say so plainly rather than failing halfway through a campaign.

Four places are searched, in order of how likely they are to be the version the
user intends:

    1. an explicit override, FAULTIVA_YOSYS / FAULTIVA_VERILATOR
    2. PATH
    3. the usual OSS CAD Suite install roots
    4. WSL, when running on Windows

The WSL case matters because characterization is a Linux step: Verilator
generates C++ and invokes a compiler, which needs a Unix toolchain.  On Windows
the honest answer is "run it inside WSL", so if a WSL distro has the tools we
report them as usable and record how to reach them.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path

WINDOWS = platform.system() == "Windows"

# OSS CAD Suite unpacks to a predictable layout; these are the roots people
# actually use, including the one this project was built against
_SUITE_HINTS = (
    "~/tools/oss-cad-suite/bin",
    "~/oss-cad-suite/bin",
    "~/.local/bin",
    "/opt/oss-cad-suite/bin",
    "/usr/local/oss-cad-suite/bin",
    "C:/oss-cad-suite/bin",
    "C:/tools/oss-cad-suite/bin",
)

_VERSION_FLAGS = {"yosys": "-V", "verilator": "--version"}
_TIMEOUT = 20

# Verilator gained --binary and --timing in 5.0; the generated testbench uses
# both, so 4.x is present-but-useless and must be reported as such.  Yosys is
# far more forgiving - 0.9 synthesizes the same netlist - but very old builds
# lack `write_json` detail the site enumerator reads.
_MINIMUM = {"yosys": (0, 9), "verilator": (5, 0)}


@dataclass
class Tool:
    """One discovered executable, or a record of its absence."""
    name: str
    found: bool = False
    path: str | None = None
    version: str | None = None
    via: str | None = None          # path | override | suite | wsl
    wsl_distro: str | None = None
    outdated: bool = False
    minimum: str | None = None

    @property
    def usable(self) -> bool:
        """Present, and new enough to run a campaign."""
        return self.found and not self.outdated

    @property
    def label(self) -> str:
        if not self.found:
            return f"{self.name} - not found"
        if self.outdated:
            return (f"{self.name} {self.version} - too old, "
                    f"needs {self.minimum}+")
        return f"{self.name} {self.version or '(version unknown)'}"


@dataclass
class Toolchain:
    """Whether this machine can characterize a circuit, and how."""
    yosys: Tool
    verilator: Tool
    platform: str = field(default_factory=lambda: platform.system())
    wsl_distro: str | None = None

    @property
    def ready(self) -> bool:
        """Both tools present AND new enough to finish a campaign."""
        return self.yosys.usable and self.verilator.usable

    @property
    def outdated(self) -> list[Tool]:
        """Tools that are installed but too old to use."""
        return [t for t in (self.yosys, self.verilator) if t.outdated]

    @property
    def needs_wsl(self) -> bool:
        """True when the only route to the tools is through WSL."""
        return self.ready and (self.yosys.via == "wsl" or self.verilator.via == "wsl")

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "needs_wsl": self.needs_wsl,
            "platform": self.platform,
            "wsl_distro": self.wsl_distro,
            "yosys": asdict(self.yosys),
            "verilator": asdict(self.verilator),
            "install_hint": install_hint(self),
        }


def _run(cmd: list[str]) -> str | None:
    """Capture a version banner, or None when the command will not run."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT,
                              creationflags=0x08000000 if WINDOWS else 0)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr or "").strip() or None


def _clean_version(name: str, banner: str | None) -> str | None:
    """Pull a short version out of a multi-line banner."""
    if not banner:
        return None
    first = banner.splitlines()[0].strip()
    if name == "yosys":
        # "Yosys 0.68+106 (git sha1 c92678eb2-dirty, ...)"
        m = re.search(r"Yosys\s+(\S+)", first)
        return m.group(1) if m else first[:40]
    # "Verilator 5.051 devel rev v5.050-222-gf6f6f8404"
    m = re.search(r"Verilator\s+(\S+)", first)
    return m.group(1) if m else first[:40]


def _as_tuple(version: str | None) -> tuple[int, ...]:
    """Leading numeric components of a version string, for comparison."""
    if not version:
        return ()
    head = re.match(r"(\d+(?:\.\d+)*)", version.lstrip("vV"))
    if not head:
        return ()
    return tuple(int(part) for part in head.group(1).split("."))


def _too_old(name: str, version: str | None) -> bool:
    """True when the tool is present but predates what the campaign needs."""
    minimum = _MINIMUM.get(name)
    parsed = _as_tuple(version)
    if not minimum or not parsed:
        return False
    return parsed[:len(minimum)] < minimum


def _mark(tool: Tool) -> Tool:
    """Attach the version verdict to a freshly probed tool."""
    minimum = _MINIMUM.get(tool.name)
    if minimum:
        tool.minimum = ".".join(str(p) for p in minimum)
    tool.outdated = _too_old(tool.name, tool.version)
    return tool


def _probe_local(name: str) -> Tool:
    """Look for a tool on this machine, outside WSL."""
    env = os.environ.get(f"FAULTIVA_{name.upper()}")
    if env and Path(env).exists():
        version = _clean_version(name, _run([env, _VERSION_FLAGS[name]]))
        if version:
            return _mark(Tool(name, True, env, version, "override"))

    # The pinned suite is preferred over PATH: characterization output is only
    # comparable to the frozen evidence when it comes from the same toolchain,
    # and a distribution package (Yosys 0.9) otherwise shadows it.
    for hint in _SUITE_HINTS:
        candidate = Path(hint).expanduser() / name
        for path in (candidate, candidate.with_suffix(".exe")):
            if path.is_file():
                version = _clean_version(name, _run([str(path), _VERSION_FLAGS[name]]))
                if version:
                    return _mark(Tool(name, True, str(path), version, "suite"))

    found = shutil.which(name)
    if found:
        version = _clean_version(name, _run([found, _VERSION_FLAGS[name]]))
        if version:
            return _mark(Tool(name, True, found, version, "path"))

    return Tool(name)


def _wsl_distros() -> list[str]:
    """Installed WSL distros, best effort."""
    if not WINDOWS:
        return []
    out = _run(["wsl.exe", "-l", "-q"])
    if not out:
        return []
    # wsl -l -q emits UTF-16; subprocess text mode leaves NULs behind
    return [d.strip() for d in out.replace("\x00", "").splitlines() if d.strip()]


def _wsl_ok(cmd: list[str]) -> bool:
    """True when the command exits zero.

    Distinct from `_run`, which reports None for an empty stdout - `test -x`
    says nothing when it succeeds, so its status is the only signal.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT,
                              creationflags=0x08000000 if WINDOWS else 0)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return proc.returncode == 0


def _wsl_home(distro: str) -> str | None:
    """The distro's home directory, resolved once and used as a literal.

    Shell variables are unusable in the probe below: `$HOME` and `$p` arrive
    at bash already expanded to nothing when a command crosses from Windows
    into WSL, so any test against them silently examines the empty string.
    """
    out = _run(["wsl.exe", "-d", distro, "--", "printenv", "HOME"])
    if not out:
        return None
    home = out.replace("\x00", "").strip().splitlines()[0].strip()
    return home or None


def _wsl_candidates(name: str, home: str | None) -> list[str]:
    """Where to look, best first.

    The pinned OSS CAD Suite leads deliberately: characterization output is
    only comparable to the frozen evidence when it comes from the same
    toolchain, so matching the pin beats finding something newer.
    """
    paths: list[str] = []
    if home:
        paths += [f"{home}/tools/oss-cad-suite/bin/{name}",
                  f"{home}/oss-cad-suite/bin/{name}"]
    paths += [f"/opt/oss-cad-suite/bin/{name}",
              f"/usr/local/oss-cad-suite/bin/{name}"]
    if home:
        paths.append(f"{home}/.local/bin/{name}")
    paths += [f"/usr/local/bin/{name}", f"/usr/bin/{name}"]
    return paths


def _probe_wsl(name: str, distro: str) -> Tool:
    """Look for a tool inside a WSL distro, preferring the pinned suite.

    Each candidate is tested as a literal argv entry - no shell, no variables,
    no loop - because variable expansion does not survive the crossing from
    Windows into WSL.
    """
    where = None
    for candidate in _wsl_candidates(name, _wsl_home(distro)):
        if _wsl_ok(["wsl.exe", "-d", distro, "--", "test", "-x", candidate]):
            where = candidate
            break

    if where is None:
        # not in any known location; accept whatever PATH offers
        out = _run(["wsl.exe", "-d", distro, "--", "command", "-v", name])
        if not out:
            return Tool(name)
        where = out.replace("\x00", "").strip().splitlines()[0].strip()
    if not where:
        return Tool(name)
    banner = _run(["wsl.exe", "-d", distro, "--", "bash", "-lc",
                   f'"{where}" {_VERSION_FLAGS[name]} 2>&1 | head -1'])
    version = _clean_version(name, (banner or "").replace("\x00", ""))
    if not version:
        return Tool(name)
    return _mark(Tool(name, True, where, version, "wsl", distro))


def detect(allow_wsl: bool = True) -> Toolchain:
    """Find Yosys and Verilator, or report what is missing."""
    yosys = _probe_local("yosys")
    verilator = _probe_local("verilator")

    if WINDOWS and allow_wsl and not (yosys.usable and verilator.usable):
        for distro in _wsl_distros():
            wy = yosys if yosys.usable else _probe_wsl("yosys", distro)
            wv = verilator if verilator.usable else _probe_wsl("verilator", distro)
            if wy.usable and wv.usable:
                return Toolchain(wy, wv, platform.system(), distro)

    return Toolchain(yosys, verilator, platform.system())


def install_hint(chain: Toolchain | None = None) -> dict:
    """What to tell the user when the tools are missing."""
    chain = chain or detect()
    if chain.ready:
        return {}

    stale = chain.outdated
    if stale:
        names = " and ".join(t.name for t in stale)
        detail = ", ".join(f"{t.name} {t.version} (needs {t.minimum}+)"
                           for t in stale)
        return {
            "command": "sudo apt install yosys verilator",
            "linux_required": True,
            "outdated": True,
            "note": (f"Found {detail}. Characterization needs a newer "
                     f"{names}; the packaged version in older distributions "
                     f"predates the simulator features it uses."),
            "windows_note": ("Linux is required for this step. On Windows, run "
                             "it inside WSL. Diagnosis works everywhere."),
        }

    return {
        "command": "sudo apt install yosys verilator",
        "linux_required": True,
        "note": ("Characterization synthesizes your Verilog and compiles a "
                 "simulator, so both tools must be present. They are free and "
                 "open source."),
        "windows_note": ("Linux is required for this step. On Windows, run it "
                         "inside WSL. Diagnosis works everywhere."),
    }


if __name__ == "__main__":
    print(json.dumps(detect().as_dict(), indent=2))
