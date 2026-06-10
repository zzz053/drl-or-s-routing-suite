#!/usr/bin/env python3
"""Build a Cython-based delivery layout for DRL-OR-S."""

from __future__ import annotations

import argparse
import importlib.machinery
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
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
    "tools/run_acceptance_verification.py",
    "setup.py",
}

TEXT_RUNTIME_FILES = {
    "README_DELIVERY.md",
    "requirements.txt",
    "start_suite.sh",
    "stop_suite.sh",
}

USER_MANUAL = Path("docs") / "cython-delivery-user-guide-cn.md"
RUNTIME_ASSET_DIRS = [
    Path("drl-or-s") / "model",
    Path("drl-or-s") / "topology",
]

CYTHON_NATIVE_SPECS = [
    {"source": "controller.py", "module": "controller_core", "target_dir": Path("."), "loader": "controller.py"},
    {"source": "server_agent.py", "module": "server_agent_core", "target_dir": Path("."), "loader": "server_agent.py"},
    {"source": "web_api.py", "module": "web_api", "target_dir": Path("."), "loader": None},
    {"source": "web_ui_html.py", "module": "web_ui_html", "target_dir": Path("."), "loader": None},
    {"source": "common_config.py", "module": "common_config", "target_dir": Path("."), "loader": None},
    {"source": "controller_helpers.py", "module": "controller_helpers", "target_dir": Path("."), "loader": None},
    {"source": "external_host_guard.py", "module": "external_host_guard", "target_dir": Path("."), "loader": None},
    {"source": "host_model.py", "module": "host_model", "target_dir": Path("."), "loader": None},
    {"source": "hybrid_gateway.py", "module": "hybrid_gateway", "target_dir": Path("."), "loader": None},
    {"source": "network_metrics.py", "module": "network_metrics", "target_dir": Path("."), "loader": None},
    {"source": "packetin_arp.py", "module": "packetin_arp", "target_dir": Path("."), "loader": None},
    {"source": "packetin_ip.py", "module": "packetin_ip", "target_dir": Path("."), "loader": None},
    {"source": "packetin_lldp.py", "module": "packetin_lldp", "target_dir": Path("."), "loader": None},
    {"source": "routing_policy.py", "module": "routing_policy", "target_dir": Path("."), "loader": None},
    {"source": "server_message_handlers.py", "module": "server_message_handlers", "target_dir": Path("."), "loader": None},
    {"source": "server_path_service.py", "module": "server_path_service", "target_dir": Path("."), "loader": None},
    {"source": "drl-or-s/path_service.py", "module": "path_service_core", "target_dir": Path("drl-or-s"), "loader": "drl-or-s/path_service.py"},
    {"source": "drl-or-s/a2c_ppo_acktr/arguments.py", "module": "a2c_ppo_acktr.arguments", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/distributions.py", "module": "a2c_ppo_acktr.distributions", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/model.py", "module": "a2c_ppo_acktr.model", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/storage.py", "module": "a2c_ppo_acktr.storage", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/utils.py", "module": "a2c_ppo_acktr.utils", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/algo/a2c_acktr.py", "module": "a2c_ppo_acktr.algo.a2c_acktr", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr" / "algo", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/algo/kfac.py", "module": "a2c_ppo_acktr.algo.kfac", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr" / "algo", "loader": None},
    {"source": "drl-or-s/a2c_ppo_acktr/algo/ppo.py", "module": "a2c_ppo_acktr.algo.ppo", "target_dir": Path("drl-or-s") / "a2c_ppo_acktr" / "algo", "loader": None},
    {"source": "drl-or-s/net_env/simenv.py", "module": "net_env.simenv", "target_dir": Path("drl-or-s") / "net_env", "loader": None},
    {"source": "drl-or-s/net_env/utils.py", "module": "net_env.utils", "target_dir": Path("drl-or-s") / "net_env", "loader": None},
]

CYTHON_SOURCE_RELS = {spec["source"] for spec in CYTHON_NATIVE_SPECS}
CYTHON_LOADER_SPECS = [spec for spec in CYTHON_NATIVE_SPECS if spec["loader"]]


def _is_excluded(path):
    parts = set(path.parts)
    return bool(parts & EXCLUDED_DIRS)


def _iter_runtime_python_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if _is_excluded(path.relative_to(ROOT)) or rel in PY_RUNTIME_EXCLUDED:
            continue
        yield path


def _copy_text_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _write_text_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _copy_non_core_python_sources(app_root):
    for source in _iter_runtime_python_files():
        rel = source.relative_to(ROOT).as_posix()
        if rel in CYTHON_SOURCE_RELS:
            continue
        target = app_root / rel
        _copy_text_file(source, target)


def _copy_non_python_runtime_assets(app_root):
    for rel in TEXT_RUNTIME_FILES:
        source = ROOT / rel
        if source.exists():
            _copy_text_file(source, app_root / rel)

    target_acceptance = app_root / "acceptance.sh"
    _copy_text_file(ROOT / "acceptance.sh", target_acceptance)
    _make_executable(target_acceptance)

    for asset_dir in RUNTIME_ASSET_DIRS:
        source_dir = ROOT / asset_dir
        if source_dir.exists():
            shutil.copytree(source_dir, app_root / asset_dir, dirs_exist_ok=True)


def _make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _loader_text(module_name, entry_point=None):
    if entry_point:
        return (
            "#!/usr/bin/env python3\n"
            f"from {module_name} import *  # noqa: F401,F403\n"
            f"from {module_name} import {entry_point} as _main\n\n"
            "if __name__ == \"__main__\":\n"
            "    raise SystemExit(_main())\n"
        )
    return (
        "#!/usr/bin/env python3\n"
        f"from {module_name} import *  # noqa: F401,F403\n"
    )


def _stage_cython_sources(stage_root):
    staged = []
    for spec in CYTHON_NATIVE_SPECS:
        source = ROOT / spec["source"]
        staged_source = stage_root / spec["source"]
        staged_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged_source)
        staged.append(
            {
                "module": spec["module"],
                "source": staged_source,
                "target_dir": spec["target_dir"].as_posix(),
                "source_rel": spec["source"],
                "loader": spec["loader"],
            }
        )
    return staged


def _write_loader_stubs(app_root):
    for spec in CYTHON_LOADER_SPECS:
        loader = spec["loader"]
        if loader == "controller.py":
            text = _loader_text("controller_core")
        elif loader == "server_agent.py":
            text = _loader_text("server_agent_core", "main")
        elif loader == "drl-or-s/path_service.py":
            text = (
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n\n"
                "_SERVICE_DIR = Path(__file__).resolve().parent\n"
                "if str(_SERVICE_DIR) not in sys.path:\n"
                "    sys.path.insert(0, str(_SERVICE_DIR))\n\n"
                "from path_service_core import *  # noqa: F401,F403\n"
                "from path_service_core import main as _main\n\n"
                "if __name__ == \"__main__\":\n"
                "    raise SystemExit(_main())\n"
            )
        else:
            continue
        target = app_root / spec["loader"]
        _write_text_file(target, text)
        _make_executable(target)


def _write_python_manifest(output_dir, staged_specs, compile_enabled):
    manifest = {
        "protection": "cython",
        "compile_enabled": bool(compile_enabled),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_executable": sys.executable,
        "sources": [
            {
                "module": spec["module"],
                "source": spec["source_rel"],
                "build_source": str(spec["source"]),
                "target_dir": spec["target_dir"],
                "loader": spec["loader"],
            }
            for spec in staged_specs
        ],
    }
    manifest_path = output_dir / "CYTHON_BUILD_MANIFEST.json"
    _write_text_file(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest_path


def _run_cython_build(staged_specs, output_dir, python_executable):
    manifest_path = _write_python_manifest(output_dir, staged_specs, True)
    build_root = Path(tempfile.mkdtemp(prefix="drl-ors-cython-build-"))
    build_lib = build_root / "lib"
    build_temp = build_root / "temp"
    build_lib.mkdir(parents=True, exist_ok=True)
    build_temp.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        str(ROOT / "setup.py"),
        "build_ext",
        "--build-lib",
        str(build_lib),
        "--build-temp",
        str(build_temp),
    ]
    env = os.environ.copy()
    env["DRL_ORS_CYTHON_MANIFEST"] = str(manifest_path)
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"python executable not found: {python_executable}") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "Cython native build failed.\n"
            f"Command: {' '.join(command)}\n"
            f"Output:\n{completed.stdout}"
        )
    return build_lib


def _compiled_artifact_for_module(build_lib, module_name):
    module_path = build_lib.joinpath(*module_name.split("."))
    parent = module_path.parent
    stem = module_path.name
    candidates = []
    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        candidates.extend(parent.glob(f"{stem}*{suffix}"))
    candidates.extend(parent.glob(f"{stem}*"))
    unique = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in unique:
            unique.append(candidate)
    if not unique:
        raise FileNotFoundError(f"compiled artifact not found for module {module_name}")
    return sorted(unique, key=lambda item: item.name)[0]


def _copy_compiled_artifacts(app_root, staged_specs, build_lib):
    for spec in staged_specs:
        artifact = _compiled_artifact_for_module(build_lib, spec["module"])
        target_dir = app_root / spec["target_dir"]
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, target_dir / artifact.name)


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


def _write_control_scripts(output_dir):
    ctl_dir = output_dir / BIN_REL
    ctl_dir.mkdir(parents=True, exist_ok=True)
    ctl = ctl_dir / "drl-orsctl"
    _write_text_file(
        ctl,
        """#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${DRL_ORS_APP_ROOT:-/opt/drl-ors}"
CONFIG="${DRL_ORS_CONFIG:-/etc/drl-ors/config.json}"
LOG_DIR="${DRL_ORS_LOG_DIR:-/var/log/drl-ors}"
REPORT_DIR="${DRL_ORS_REPORT_DIR:-/var/lib/drl-ors/reports}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MININET_PYTHON="${MININET_PYTHON:-python3}"

export PYTHONPATH="$APP_ROOT:${PYTHONPATH:-}"
export ACCEPTANCE_CONFIG="$CONFIG"
export LOG_DIR REPORT_DIR PYTHON_BIN MININET_PYTHON

mkdir -p "$LOG_DIR" "$REPORT_DIR"
cd "$APP_ROOT"
exec bash ./acceptance.sh "$@"
""",
    )
    _make_executable(ctl)

    unit_dir = output_dir / SYSTEMD_REL
    unit_dir.mkdir(parents=True, exist_ok=True)
    _write_text_file(
        unit_dir / "drl-ors.service",
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


def _write_delivery_notes(output_dir, compile_enabled):
    runtime = output_dir / "PYTHON_RUNTIME.txt"
    _write_text_file(
        runtime,
        "DRL-OR-S delivery runtime\n"
        "protection=cython\n"
        f"compile_enabled={str(bool(compile_enabled)).lower()}\n"
        f"python_version={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n"
        f"python_executable={sys.executable}\n"
        "note=Build this package on Linux with the same Python major/minor version as the target runtime.\n",
    )

    manual_source = ROOT / USER_MANUAL
    if manual_source.exists():
        shutil.copy2(manual_source, output_dir / "README_USER_MANUAL_CN.md")

    _write_text_file(
        output_dir / "README_DELIVERY_RUNTIME.md",
        """# DRL-OR-S Runtime Delivery

This package keeps first-party core Python source out of the runtime tree.
Core modules are compiled with Cython; editable deployment settings live under
`/etc/drl-ors/config.json`, and operational scripts remain readable.

Install by copying the package root onto the target filesystem, then use:

```bash
drl-orsctl start
drl-orsctl health
drl-orsctl report
drl-orsctl stop
```

This is a source-protection measure, not absolute cryptographic security. Root
users can still copy or reverse engineer runtime artifacts.
""",
    )


def build_delivery_package(
    output_dir,
    config_path=ROOT / "config" / "hybrid_acceptance.json",
    force=True,
    compile_extensions=True,
    protection="cython",
    python_executable=sys.executable,
):
    if protection != "cython":
        raise ValueError("only cython protection is supported")

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
    _copy_non_core_python_sources(app_root)
    _write_loader_stubs(app_root)

    staged_specs = _stage_cython_sources(Path(tempfile.mkdtemp(prefix="drl-ors-cython-stage-")))
    if compile_extensions:
        build_lib = _run_cython_build(staged_specs, output_dir, python_executable)
        _copy_compiled_artifacts(app_root, staged_specs, build_lib)
    else:
        _write_python_manifest(output_dir, staged_specs, False)

    _copy_configs(config_root, config_path)
    _write_control_scripts(output_dir)
    _write_delivery_notes(output_dir, compile_extensions)
    return output_dir


def write_delivery_zip(package_dir, zip_path):
    package_dir = Path(package_dir)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir).as_posix())
    return zip_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a Cython-protected DRL-OR-S delivery package.")
    parser.add_argument("--output", required=True, help="Output directory to create.")
    parser.add_argument("--config", default=str(ROOT / "config" / "hybrid_acceptance.json"))
    parser.add_argument("--no-force", action="store_true", help="Fail if output already exists.")
    parser.add_argument("--zip", dest="zip_path", help="Optional zip file to write with Linux-compatible paths.")
    parser.add_argument("--no-compile", action="store_true", help="Stage the package without compiling native extensions.")
    parser.add_argument("--protection", default="cython", choices=["cython"], help="Protection backend to use.")
    parser.add_argument("--python", default=sys.executable, help="Python executable used for Cython compilation.")
    args = parser.parse_args(argv)

    output_dir = build_delivery_package(
        args.output,
        config_path=args.config,
        force=not args.no_force,
        compile_extensions=not args.no_compile,
        protection=args.protection,
        python_executable=args.python,
    )
    print(f"delivery package written to {args.output}")
    if args.zip_path:
        write_delivery_zip(output_dir, args.zip_path)
        print(f"delivery zip written to {args.zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
