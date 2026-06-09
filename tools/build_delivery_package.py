#!/usr/bin/env python3
"""Build a simple source-hidden delivery layout for DRL-OR-S."""

import argparse
import shutil
import stat
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_REL = Path("opt") / "drl-ors"
CONFIG_REL = Path("etc") / "drl-ors"
BIN_REL = Path("usr") / "local" / "bin"
SYSTEMD_REL = Path("etc") / "systemd" / "system"

EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".vscode",
    "__pycache__",
    "delivery",
    "docs",
    "dist",
    "logs",
    "reports",
    "tests",
}

PY_RUNTIME_EXCLUDED = {
    "tools/build_delivery_package.py",
}

TEXT_RUNTIME_FILES = {
    "README_DELIVERY.md",
    "requirements.txt",
    "start_suite.sh",
    "stop_suite.sh",
}
RUNTIME_ASSET_DIRS = [
    Path("drl-or-s") / "model",
    Path("drl-or-s") / "topology",
]

PY_ENTRYPOINTS = [
    "drl-or-s/path_service.py",
    "server_agent.py",
    "start_controllers_test.py",
    "testbed/creat_test_topo.py",
    "tools/acceptance_config.py",
    "tools/acceptance_feature_audit.py",
    "tools/acceptance_health.py",
    "tools/generate_acceptance_report.py",
    "tools/mininet_load_test.py",
]


def _is_excluded(path):
    parts = set(path.parts)
    return bool(parts & EXCLUDED_DIRS)


def _iter_runtime_python_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if _is_excluded(path.relative_to(ROOT)) or rel in PY_RUNTIME_EXCLUDED:
            continue
        yield path


def _copy_non_python_runtime_assets(app_root):
    for rel in TEXT_RUNTIME_FILES:
        source = ROOT / rel
        if source.exists():
            target = app_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    acceptance = (ROOT / "acceptance.sh").read_text(encoding="utf-8")
    for entrypoint in PY_ENTRYPOINTS:
        acceptance = acceptance.replace(entrypoint, entrypoint[:-3] + ".pyc")
    target_acceptance = app_root / "acceptance.sh"
    with target_acceptance.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(acceptance)
    _make_executable(target_acceptance)

    for asset_dir in RUNTIME_ASSET_DIRS:
        source_dir = ROOT / asset_dir
        if source_dir.exists():
            shutil.copytree(source_dir, app_root / asset_dir, dirs_exist_ok=True)


def _compile_runtime_python(app_root):
    for source in _iter_runtime_python_files():
        rel = source.relative_to(ROOT)
        target = app_root / rel.with_suffix(".pyc")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            py_compile.compile(str(source), cfile=str(target), doraise=True)
        except py_compile.PyCompileError as exc:
            raise RuntimeError(f"failed to compile {source}")


def _copy_configs(config_root, config_path):
    config_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, config_root / "config.json")
    for name in [
        "hybrid_acceptance.json",
        "hybrid_acceptance.vm.json",
        "hybrid_acceptance.server.json",
    ]:
        source = ROOT / "config" / name
        if source.exists():
            shutil.copy2(source, config_root / name)


def _make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_control_scripts(output_dir):
    ctl_dir = output_dir / BIN_REL
    ctl_dir.mkdir(parents=True, exist_ok=True)
    ctl = ctl_dir / "drl-orsctl"
    with ctl.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            """#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${DRL_ORS_APP_ROOT:-/opt/drl-ors}"
CONFIG="${DRL_ORS_CONFIG:-/etc/drl-ors/config.json}"
LOG_DIR="${DRL_ORS_LOG_DIR:-/var/log/drl-ors}"
REPORT_DIR="${DRL_ORS_REPORT_DIR:-/var/lib/drl-ors/reports}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MININET_PYTHON="${MININET_PYTHON:-python3}"

export ACCEPTANCE_CONFIG="$CONFIG"
export LOG_DIR REPORT_DIR PYTHON_BIN MININET_PYTHON

mkdir -p "$LOG_DIR" "$REPORT_DIR"
cd "$APP_ROOT"
exec ./acceptance.sh "$@"
""",
        )
    _make_executable(ctl)

    unit_dir = output_dir / SYSTEMD_REL
    unit_dir.mkdir(parents=True, exist_ok=True)
    with (unit_dir / "drl-ors.service").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            """[Unit]
Description=DRL-OR-S acceptance runtime
After=network-online.target openvswitch-switch.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/drl-orsctl start
ExecStop=/usr/local/bin/drl-orsctl stop
TimeoutStartSec=360
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
""",
        )


def _write_delivery_notes(output_dir):
    notes = output_dir / "README_DELIVERY_RUNTIME.md"
    with notes.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            """# DRL-OR-S Runtime Delivery

This package keeps first-party Python source out of the runtime tree. Core
modules are shipped as `.pyc`; editable deployment settings live under
`/etc/drl-ors/config.json`.

Install by copying the package root onto the target filesystem, then use:

```bash
drl-orsctl start
drl-orsctl health
drl-orsctl report
drl-orsctl stop
```

This is an operational hiding measure, not cryptographic source protection.
Root users can still inspect, copy, or reverse engineer runtime artifacts.
"""
        )


def build_delivery_package(output_dir, config_path=ROOT / "config" / "hybrid_acceptance.json", force=True):
    output_dir = Path(output_dir)
    config_path = Path(config_path)
    if output_dir.exists():
        if not force:
            raise FileExistsError(output_dir)
        shutil.rmtree(output_dir)

    app_root = output_dir / APP_REL
    config_root = output_dir / CONFIG_REL
    app_root.mkdir(parents=True, exist_ok=True)
    (output_dir / "var" / "log" / "drl-ors").mkdir(parents=True, exist_ok=True)
    (output_dir / "var" / "lib" / "drl-ors" / "reports").mkdir(parents=True, exist_ok=True)

    _copy_non_python_runtime_assets(app_root)
    _compile_runtime_python(app_root)
    _copy_configs(config_root, config_path)
    _write_control_scripts(output_dir)
    _write_delivery_notes(output_dir)
    return output_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a source-hidden DRL-OR-S delivery package.")
    parser.add_argument("--output", required=True, help="Output directory to create.")
    parser.add_argument("--config", default=str(ROOT / "config" / "hybrid_acceptance.json"))
    parser.add_argument("--no-force", action="store_true", help="Fail if output already exists.")
    args = parser.parse_args(argv)

    build_delivery_package(args.output, config_path=args.config, force=not args.no_force)
    print(f"delivery package written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
