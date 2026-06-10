import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "run_acceptance_verification",
        ROOT / "tools" / "run_acceptance_verification.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_defaults_match_acceptance_vm():
    runner = load_runner()

    args = runner.parse_args(["--local-only"])

    assert args.vm == "192.168.172.128"
    assert args.user == "hydrate"
    assert args.repo == "/home/hydrate/a/drl-or-s-routing-suite"
    assert args.wrapper == "/home/hydrate/run_drl_ors_conda.sh"
    assert args.local_only is True


def test_vm_commands_always_use_conda_wrapper():
    runner = load_runner()
    args = runner.parse_args(["--skip-health"])

    command = runner.build_vm_command(args, ["python", "-m", "pytest", "-q"])

    remote_command = command[-1]
    assert command[:2] == ["ssh", "hydrate@192.168.172.128"]
    assert "cd /home/hydrate/a/drl-or-s-routing-suite" in remote_command
    assert "/home/hydrate/run_drl_ors_conda.sh python -m pytest -q" in remote_command


def test_sudo_password_is_injected_only_from_environment(monkeypatch):
    runner = load_runner()
    args = runner.parse_args([])

    monkeypatch.delenv("DRL_ORS_VM_PASSWORD", raising=False)
    assert "SUDO_PASSWORD=" not in runner.build_vm_acceptance_command(args, "health")[-1]

    monkeypatch.setenv("DRL_ORS_VM_PASSWORD", "secret")
    remote_command = runner.build_vm_acceptance_command(args, "health")[-1]
    assert "SUDO_PASSWORD=secret" in remote_command
    assert "h" not in os.environ.get("DRL_ORS_VM_PASSWORD", "")


def test_sync_changed_filter_excludes_local_cache_and_user_materials():
    runner = load_runner()
    porcelain = [
        " M tools/build_delivery_package.py",
        "?? tests/test_acceptance_verification_runner.py",
        "?? docs/delivery-user-manual-cn.md",
        "?? .codegraph/codegraph.db",
        "?? NUL",
        "?? 新的交换机有关命令.docx",
        "?? Abi拓扑虚实交换机通信部署说明.md",
    ]

    files = runner.select_sync_files(porcelain)

    assert "tools/build_delivery_package.py" in files
    assert "tests/test_acceptance_verification_runner.py" in files
    assert "docs/delivery-user-manual-cn.md" in files
    assert ".codegraph/codegraph.db" not in files
    assert "NUL" not in files
    assert "新的交换机有关命令.docx" not in files
    assert "Abi拓扑虚实交换机通信部署说明.md" not in files


def test_delivery_smoke_expectations_match_cython_layout():
    runner = load_runner()

    assert "CYTHON_BUILD_MANIFEST.json" in runner.DELIVERY_REQUIRED_FILES
    assert "opt/drl-ors/controller.py" in runner.DELIVERY_REQUIRED_FILES
    assert "opt/drl-ors/server_agent.py" in runner.DELIVERY_REQUIRED_FILES
    assert "opt/drl-ors/drl-or-s/path_service.py" in runner.DELIVERY_REQUIRED_FILES
    assert "opt/drl-ors/controller_core*.so" in runner.DELIVERY_REQUIRED_GLOBS
    assert "opt/drl-ors/server_agent_core*.so" in runner.DELIVERY_REQUIRED_GLOBS
    assert "opt/drl-ors/drl-or-s/path_service_core*.so" in runner.DELIVERY_REQUIRED_GLOBS
    assert "opt/drl-ors/controller.py" not in runner.DELIVERY_LEAKED_SOURCE_CANDIDATES
    assert "opt/drl-ors/server_agent.py" not in runner.DELIVERY_LEAKED_SOURCE_CANDIDATES
    assert runner.DELIVERY_LOADER_MARKERS["opt/drl-ors/server_agent.py"] == [
        "from server_agent_core import *",
        "from server_agent_core import main as _main",
    ]
    assert "class ServerAgent" in runner.DELIVERY_RAW_MARKER_LEAKS["opt/drl-ors/server_agent.py"]
