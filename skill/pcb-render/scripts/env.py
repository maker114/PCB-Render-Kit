"""Locate Blender (and read optional user config) for the pcb-render scripts.

Nothing in this repo hard-codes a machine path. Resolution order for Blender:

  1. an explicit path passed by the caller (`--blender`)
  2. the `blender` key of a config file (see CONFIG below)
  3. the PCB_RENDER_BLENDER environment variable
  4. `blender` on PATH
  5. the usual install locations for the current OS (incl. Steam libraries)

Config file lookup order (JSON, every key optional):

  $PCB_RENDER_CONFIG
  <repo>/skill/pcb-render/config.json
  %APPDATA%/pcb-render/config.json          (Windows)
  ~/.config/pcb-render/config.json          (macOS / Linux)

Example:

  { "blender": "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" }

CLI:

  python env.py                 # print a JSON report of what was found
  python env.py --print-blender # print only the Blender path (for shell scripts)
"""

from __future__ import annotations

import glob
import json
import os
import platform
import shutil
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- config
def config_paths():
    """Candidate config files, most specific first."""
    out = []
    env_cfg = os.environ.get("PCB_RENDER_CONFIG")
    if env_cfg:
        out.append(env_cfg)
    out.append(os.path.join(SKILL_ROOT, "config.json"))
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(os.path.join(appdata, "pcb-render", "config.json"))
    else:
        out.append(os.path.join(os.path.expanduser("~"), ".config", "pcb-render", "config.json"))
    return out


def load_config():
    """First readable config file as a dict; empty dict when there is none."""
    for path in config_paths():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data["_source"] = path
                return data
        except (OSError, ValueError):
            continue
    return {}


# --------------------------------------------------------------------------- blender
def _steam_blender_paths():
    """blender.exe inside every Steam library, via libraryfolders.vdf (Windows)."""
    if os.name != "nt":
        return []
    steam_roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_roots.append(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        pass
    # 其他盘符的 Steam 库由 libraryfolders.vdf 覆盖，这里只补默认安装位置
    steam_roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]

    found = []
    for root in steam_roots:
        if not root or not os.path.isdir(root):
            continue
        candidates = [os.path.join(root, "steamapps", "common", "Blender")] + [
            os.path.join(line.strip().strip('"'), "steamapps", "common", "Blender")
            for line in _library_paths(os.path.join(root, "steamapps", "libraryfolders.vdf"))
        ]
        for folder in candidates:
            exe = os.path.join(folder, "blender.exe")
            if os.path.isfile(exe):
                found.append(exe)
    return found


def _library_paths(vdf_path):
    """Yield 'path' values from a Steam libraryfolders.vdf (regex, no deps)."""
    import re
    try:
        with open(vdf_path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        yield match.group(1).replace("\\\\", "\\")


def candidate_blender_paths():
    """Every location worth trying, in kernel order."""
    system = platform.system()
    out = []

    if system == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if not base:
                continue
            # newest version first
            for pattern in (os.path.join(base, "Blender Foundation", "Blender *", "blender.exe"),
                            os.path.join(base, "Programs", "Blender*", "blender.exe"),
                            os.path.join(base, "Steam", "steamapps", "common", "Blender", "blender.exe")):
                out += sorted(glob.glob(pattern), reverse=True)
        out += _steam_blender_paths()
    elif system == "Darwin":
        out += ["/Applications/Blender.app/Contents/MacOS/Blender",
                os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender")]
        out += sorted(glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender"), reverse=True)
    else:
        out += ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",
                "/var/lib/flatpak/exports/bin/org.blender.Blender",
                os.path.expanduser("~/.local/bin/blender")]

    return [p for p in out if p and os.path.isfile(p)]


def find_blender(explicit: str | None = None) -> str:
    """Return an existing Blender executable, or raise SystemExit with guidance."""
    if explicit:
        if os.path.isfile(explicit):
            return os.path.abspath(explicit)
        raise SystemExit(f"Blender not found at the path you gave: {explicit}")

    cfg = load_config()
    if cfg.get("blender"):
        path = os.path.expanduser(str(cfg["blender"]))
        if os.path.isfile(path):
            return os.path.abspath(path)
        print(f"[env] warning: config says blender={path!r} but it does not exist; "
              f"falling back to auto-detect", file=sys.stderr)

    env_path = os.environ.get("PCB_RENDER_BLENDER")
    if env_path and os.path.isfile(env_path):
        return os.path.abspath(env_path)

    which = shutil.which("blender")
    if which:
        return which

    found = candidate_blender_paths()
    if found:
        return found[0]

    raise SystemExit(
        "Blender not found.\n"
        "  - install Blender 4.x/5.x (https://www.blender.org/download/) and put it on PATH, or\n"
        "  - pass --blender \"<path to blender executable>\", or\n"
        "  - write it into a config file, e.g. %APPDATA%\\pcb-render\\config.json:\n"
        '        { "blender": "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" }\n'
        "  (alternatively set the PCB_RENDER_BLENDER environment variable)"
    )


# --------------------------------------------------------------------------- deps
REQUIRED = (("numpy", "numpy"), ("PIL", "Pillow"), ("ezdxf", "ezdxf"), ("cascadio", "cascadio"))


def require_python_deps():
    """Fail early with an actionable message instead of a bare ImportError."""
    missing = []
    for module, package in REQUIRED:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        raise SystemExit(
            "缺少 Python 依赖: " + ", ".join(missing) + "\n"
            f"  当前解释器: {sys.executable}\n"
            f"  安装: {sys.executable} -m pip install -r "
            f"{os.path.join(SKILL_ROOT, 'requirements.txt')}"
        )


# --------------------------------------------------------------------------- report
def report():
    cfg = load_config()
    try:
        blender = find_blender()
        blender_err = None
    except SystemExit as exc:
        blender, blender_err = None, str(exc).splitlines()[0]
    return {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.executable,
        "python_version": platform.python_version(),
        "skill_root": SKILL_ROOT,
        "config": cfg.get("_source", "(none)"),
        "blender": blender,
        "blender_error": blender_err,
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--print-blender" in argv:
        print(find_blender())
        return 0
    print(json.dumps(report(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
