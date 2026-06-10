#!/usr/bin/env python3
"""Run repeatable local and VM acceptance verification for DRL-OR-S."""

import argparse
import os
import platform
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VM = "192.168.172.128"
DEFAULT_USER = "hydrate"
DEFAULT_REPO = "/home/hydrate/a/drl-or-s-routing-suite"
DEFAULT_WRAPPER = "/home/hydrate/run_drl_ors_conda.sh"
PASSWORD_ENV = "DRL_ORS_VM_PASSWORD"

EXCLUDED_SYNC_PREFIXES = (
    ".codegraph/",
    ".git/",
    ".pytest_cache/",
    "__pycache__/",
    "delivery/",
    "dist/",
    "logs/",
)
EXCLUDED_SYNC_SUFFIXES = (".docx",)

DELIVERY_REQUIRED_FILES = [
    "CYTHON_BUILD_MANIFEST.json",
    "README_USER_MANUAL_CN.md",
    "opt/drl-ors/controller.py",
    "opt/drl-ors/server_agent.py",
    "opt/drl-ors/drl-or-s/path_service.py",
    "etc/drl-ors/config.json",
    "usr/local/bin/drl-orsctl",
]
DELIVERY_REQUIRED_GLOBS = [
    "opt/drl-ors/controller_core*.so",
    "opt/drl-ors/server_agent_core*.so",
    "opt/drl-ors/drl-or-s/path_service_core*.so",
]
DELIVERY_LEAKED_SOURCE_CANDIDATES = [
    "opt/drl-ors/controller_core.py",
    "opt/drl-ors/server_agent_core.py",
    "opt/drl-ors/drl-or-s/path_service_core.py",
    "opt/drl-ors/controller.pyc",
    "opt/drl-ors/server_agent.pyc",
    "opt/drl-ors/drl-or-s/path_service.pyc",
]
DELIVERY_LOADER_MARKERS = {
    "opt/drl-ors/controller.py": ["from controller_core import *"],
    "opt/drl-ors/server_agent.py": ["from server_agent_core import *", "from server_agent_core import main as _main"],
    "opt/drl-ors/drl-or-s/path_service.py": ["from path_service_core import *", "from path_service_core import main as _main"],
}
DELIVERY_RAW_MARKER_LEAKS = {
    "opt/drl-ors/controller.py": ["class TopoAwareness"],
    "opt/drl-ors/server_agent.py": ["class ServerAgent", "def add_manual_flow"],
    "opt/drl-ors/drl-or-s/path_service.py": ["class DRLPathService"],
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run local tests, optional VM acceptance checks, and delivery package smoke checks.",
    )
    parser.add_argument("--local-only", action="store_true", help="Run only local pytest/audit/package smoke checks.")
    parser.add_argument("--vm", default=DEFAULT_VM, help="Acceptance VM IP address.")
    parser.add_argument("--user", default=DEFAULT_USER, help="Acceptance VM SSH user.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="Repo path on the acceptance VM.")
    parser.add_argument("--wrapper", default=DEFAULT_WRAPPER, help="Conda wrapper path on the acceptance VM.")
    parser.add_argument("--sync-changed", action="store_true", help="Sync intended changed files before VM checks.")
    parser.add_argument("--skip-health", action="store_true", help="Skip VM acceptance.sh stop/start/health.")
    parser.add_argument("--skip-delivery", action="store_true", help="Skip local delivery package smoke check.")
    return parser.parse_args(argv)


def _quote(value):
    return shlex.quote(str(value))


def build_vm_command(args, command):
    remote_command = (
        f"cd {_quote(args.repo)} && "
        f"{_quote(args.wrapper)} {' '.join(_quote(part) for part in command)}"
    )
    return ["ssh", f"{args.user}@{args.vm}", remote_command]


def build_vm_acceptance_command(args, action):
    password = os.environ.get(PASSWORD_ENV)
    password_prefix = f"SUDO_PASSWORD={_quote(password)} " if password else ""
    remote_command = (
        f"cd {_quote(args.repo)} && "
        f"{password_prefix}{_quote(args.wrapper)} ./acceptance.sh {_quote(action)}"
    )
    return ["ssh", f"{args.user}@{args.vm}", remote_command]


def _run_checked(command, cwd=ROOT, timeout=None):
    print(f"$ {' '.join(str(part) for part in command)}")
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if completed.stdout:
        output = completed.stdout
        encoding = sys.stdout.encoding or "utf-8"
        try:
            sys.stdout.buffer.write(output.encode(encoding, errors="replace"))
            if not output.endswith("\n"):
                sys.stdout.buffer.write(b"\n")
            sys.stdout.flush()
        except AttributeError:
            print(output.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {command}")
    return completed


def run_local_verification(skip_delivery=False):
    _run_checked([sys.executable, "-m", "pytest", "-q"], timeout=180)
    _run_checked([sys.executable, "tools/acceptance_feature_audit.py"], timeout=60)
    if not skip_delivery:
        run_delivery_smoke()


def run_delivery_smoke():
    with tempfile.TemporaryDirectory(prefix="drl-ors-delivery-") as tmp:
        output_dir = Path(tmp) / "runtime"
        compile_extensions = platform.system().lower() == "linux"
        build_command = [sys.executable, "tools/build_delivery_package.py", "--output", str(output_dir)]
        if not compile_extensions:
            build_command.append("--no-compile")
        _run_checked(
            build_command,
            timeout=360,
        )
        required = [output_dir / rel for rel in DELIVERY_REQUIRED_FILES]
        missing = [str(path) for path in required if not path.exists()]
        missing_globs = []
        if compile_extensions:
            missing_globs = [
                pattern
                for pattern in DELIVERY_REQUIRED_GLOBS
                if not list(output_dir.glob(pattern))
            ]
        leaked_sources = [output_dir / rel for rel in DELIVERY_LEAKED_SOURCE_CANDIDATES]
        leaked = [str(path) for path in leaked_sources if path.exists()]
        missing_loader_markers = []
        for rel, markers in DELIVERY_LOADER_MARKERS.items():
            text = (output_dir / rel).read_text(encoding="utf-8", errors="replace")
            for marker in markers:
                if marker not in text:
                    missing_loader_markers.append(f"{rel} missing loader marker {marker!r}")
        marker_leaks = []
        for rel, markers in DELIVERY_RAW_MARKER_LEAKS.items():
            text = (output_dir / rel).read_text(encoding="utf-8", errors="replace")
            for marker in markers:
                if marker in text:
                    marker_leaks.append(f"{rel} contains raw marker {marker!r}")
        if missing or missing_globs or leaked:
            raise RuntimeError(
                "delivery smoke failed, "
                f"missing={missing}, missing_globs={missing_globs}, leaked_sources={leaked}"
            )
        if missing_loader_markers:
            raise RuntimeError(f"delivery smoke failed, missing_loader_markers={missing_loader_markers}")
        if marker_leaks:
            raise RuntimeError(f"delivery smoke failed, marker_leaks={marker_leaks}")
        print("delivery package smoke: pass")


def _path_from_porcelain_line(line):
    if not line:
        return ""
    path = line[3:] if len(line) > 3 else ""
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"').replace("\\", "/")


def _has_non_ascii(path):
    return any(ord(ch) > 127 for ch in path)


def _is_sync_candidate(status, path):
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == "NUL":
        return False
    if any(normalized.startswith(prefix) for prefix in EXCLUDED_SYNC_PREFIXES):
        return False
    if any(part == "__pycache__" for part in normalized.split("/")):
        return False
    if normalized.lower().endswith(EXCLUDED_SYNC_SUFFIXES):
        return False
    if status == "??" and _has_non_ascii(normalized):
        return False
    return True


def select_sync_files(porcelain_lines):
    files = []
    for line in porcelain_lines:
        status = line[:2]
        path = _path_from_porcelain_line(line)
        if _is_sync_candidate(status, path):
            files.append(path)
    return files


def collect_changed_files():
    completed = _run_checked(["git", "status", "--porcelain"], timeout=30)
    return select_sync_files(completed.stdout.splitlines())


def sync_changed_files(args):
    files = collect_changed_files()
    if not files:
        print("sync changed files: no eligible files")
        return
    for rel in files:
        local_path = ROOT / rel
        if not local_path.is_file():
            continue
        remote_parent = str(PurePosixPath(args.repo) / PurePosixPath(rel).parent)
        remote_path = str(PurePosixPath(args.repo) / PurePosixPath(rel))
        _run_checked(["ssh", f"{args.user}@{args.vm}", f"mkdir -p {_quote(remote_parent)}"], timeout=30)
        _run_checked(["scp", str(local_path), f"{args.user}@{args.vm}:{remote_path}"], timeout=60)


def run_vm_verification(args):
    if args.sync_changed:
        sync_changed_files(args)
    _run_checked(build_vm_command(args, ["python", "-m", "pytest", "-q"]), timeout=240)
    _run_checked(build_vm_command(args, ["python", "tools/acceptance_feature_audit.py"]), timeout=120)
    _run_checked(
        build_vm_command(
            args,
            [
                "python",
                "tools/build_delivery_package.py",
                "--output",
                "dist/drl-ors-runtime-cython-smoke",
                "--protection",
                "cython",
            ],
        ),
        timeout=600,
    )
    if args.skip_health:
        return
    if not os.environ.get(PASSWORD_ENV):
        raise RuntimeError(f"{PASSWORD_ENV} must be set before running VM acceptance health checks")
    for action in ("stop", "start", "health"):
        _run_checked(build_vm_acceptance_command(args, action), timeout=360)


def main(argv=None):
    args = parse_args(argv)
    run_local_verification(skip_delivery=args.skip_delivery)
    if not args.local_only:
        run_vm_verification(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
