import importlib.util
import sys as runtime_sys
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "build_delivery_package",
        ROOT / "tools" / "build_delivery_package.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_cython_build(builder, calls):
    def _fake_run_cython_build(staged_specs, output_dir, python_executable):
        calls["python_executable"] = python_executable
        calls["modules"] = [spec["module"] for spec in staged_specs]
        builder._write_python_manifest(output_dir, staged_specs, True)
        build_lib = output_dir / "_fake_build_lib"
        for spec in staged_specs:
            module_path = Path(*spec["module"].split("."))
            target = build_lib / module_path.parent / f"{module_path.name}.so"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"native-extension")
        return build_lib

    return _fake_run_cython_build


def test_delivery_package_hides_core_python_sources_and_exposes_config(tmp_path, monkeypatch):
    builder = load_builder()
    output_dir = tmp_path / "delivery"
    calls = {}
    monkeypatch.setattr(builder, "_run_cython_build", fake_cython_build(builder, calls))

    builder.build_delivery_package(
        output_dir,
        config_path=ROOT / "config" / "hybrid_acceptance.json",
        python_executable="python-cython-test",
    )

    app_root = output_dir / "opt" / "drl-ors"
    config_root = output_dir / "etc" / "drl-ors"
    ctl = output_dir / "usr" / "local" / "bin" / "drl-orsctl"

    assert (config_root / "config.json").exists()
    assert (config_root / "hybrid_acceptance.vm.json").exists()
    assert (output_dir / "CYTHON_BUILD_MANIFEST.json").exists()
    assert ctl.exists()
    ctl_text = ctl.read_text(encoding="utf-8")
    assert "ACCEPTANCE_CONFIG" in ctl_text
    assert 'export PYTHONPATH="$APP_ROOT:${PYTHONPATH:-}"' in ctl_text
    assert 'exec bash ./acceptance.sh "$@"' in ctl_text

    assert calls["python_executable"] == "python-cython-test"
    for module in [
        "controller_core",
        "server_agent_core",
        "path_service_core",
        "server_path_service",
        "routing_policy",
        "network_metrics",
        "a2c_ppo_acktr.model",
        "net_env.simenv",
    ]:
        assert module in calls["modules"]

    assert (app_root / "controller_core.so").exists()
    assert (app_root / "server_agent_core.so").exists()
    assert (app_root / "drl-or-s" / "path_service_core.so").exists()
    assert (app_root / "server_path_service.so").exists()

    for leaked_source in [
        app_root / "controller_core.py",
        app_root / "server_agent_core.py",
        app_root / "drl-or-s" / "path_service_core.py",
        app_root / "server_path_service.py",
        app_root / "drl-or-s" / "a2c_ppo_acktr" / "model.py",
        app_root / "drl-or-s" / "net_env" / "simenv.py",
    ]:
        assert not leaked_source.exists(), f"core source leaked into Cython package: {leaked_source}"

    controller_loader = (app_root / "controller.py").read_text(encoding="utf-8")
    assert "from controller_core import *" in controller_loader
    assert "class TopoAwareness" not in controller_loader

    server_agent_loader = (app_root / "server_agent.py").read_text(encoding="utf-8")
    assert "from server_agent_core import main as _main" in server_agent_loader
    assert "class ServerAgent" not in server_agent_loader

    path_loader = (app_root / "drl-or-s" / "path_service.py").read_text(encoding="utf-8")
    assert "from path_service_core import main as _main" in path_loader
    assert "class DRLPathService" not in path_loader

    non_core_source = (app_root / "tools" / "acceptance_config.py").read_text(encoding="utf-8")
    assert "def load_acceptance_config" in non_core_source
    assert not (app_root / "tools" / "run_acceptance_verification.py").exists()

    assert not (output_dir / ".git").exists()
    assert not (app_root / "tests").exists()
    assert (app_root / "drl-or-s" / "model" / "Military_mininet" / "agent0.pth").exists()
    acceptance_text = (app_root / "acceptance.sh").read_text(encoding="utf-8")
    assert "drl-or-s/path_service.py" in acceptance_text
    assert "server_agent.py" in acceptance_text
    assert 'export PYTHONPATH="$PWD:${PYTHONPATH:-}"' in acceptance_text
    assert ".pyc" not in acceptance_text
    assert (output_dir / "README_USER_MANUAL_CN.md").exists()
    runtime_text = (output_dir / "PYTHON_RUNTIME.txt").read_text(encoding="utf-8")
    assert f"{runtime_sys.version_info.major}.{runtime_sys.version_info.minor}" in runtime_text
    assert "protection=cython" in runtime_text
    manual_text = (output_dir / "README_USER_MANUAL_CN.md").read_text(encoding="utf-8")
    assert "Cython" in manual_text
    assert "drl-orsctl start" in manual_text


def test_delivery_package_can_stage_without_native_compile(tmp_path):
    builder = load_builder()
    output_dir = tmp_path / "delivery"

    builder.build_delivery_package(
        output_dir,
        config_path=ROOT / "config" / "hybrid_acceptance.json",
        compile_extensions=False,
    )

    manifest_text = (output_dir / "CYTHON_BUILD_MANIFEST.json").read_text(encoding="utf-8")
    assert '"compile_enabled": false' in manifest_text
    assert (output_dir / "opt" / "drl-ors" / "controller.py").exists()
    assert not list((output_dir / "opt" / "drl-ors").glob("controller_core*.so"))


def test_delivery_zip_uses_posix_paths_for_linux_extraction(tmp_path, monkeypatch):
    builder = load_builder()
    output_dir = tmp_path / "delivery"
    zip_path = tmp_path / "delivery.zip"
    monkeypatch.setattr(builder, "_run_cython_build", fake_cython_build(builder, {}))

    builder.build_delivery_package(output_dir, config_path=ROOT / "config" / "hybrid_acceptance.json")
    builder.write_delivery_zip(output_dir, zip_path)

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()

    assert "opt/drl-ors/acceptance.sh" in names
    assert "opt/drl-ors/controller_core.so" in names
    assert "opt/drl-ors/drl-or-s/path_service_core.so" in names
    assert "etc/drl-ors/config.json" in names
    assert all("\\" not in name for name in names)
