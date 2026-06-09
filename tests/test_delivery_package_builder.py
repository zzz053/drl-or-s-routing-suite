import importlib.util
import sys
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


def test_delivery_package_hides_core_python_sources_and_exposes_config(tmp_path):
    builder = load_builder()
    output_dir = tmp_path / "delivery"

    builder.build_delivery_package(output_dir, config_path=ROOT / "config" / "hybrid_acceptance.json")

    app_root = output_dir / "opt" / "drl-ors"
    config_root = output_dir / "etc" / "drl-ors"
    ctl = output_dir / "usr" / "local" / "bin" / "drl-orsctl"

    assert (config_root / "config.json").exists()
    assert (config_root / "hybrid_acceptance.vm.json").exists()
    assert ctl.exists()
    assert "ACCEPTANCE_CONFIG" in ctl.read_text(encoding="utf-8")

    for source in [
        app_root / "controller.py",
        app_root / "server_agent.py",
        app_root / "web_api.py",
        app_root / "drl-or-s" / "path_service.py",
        app_root / "tools" / "acceptance_config.py",
    ]:
        assert not source.exists(), f"source file leaked into delivery package: {source}"

    for bytecode in [
        app_root / "controller.pyc",
        app_root / "server_agent.pyc",
        app_root / "web_api.pyc",
        app_root / "drl-or-s" / "path_service.pyc",
        app_root / "tools" / "acceptance_config.pyc",
    ]:
        assert bytecode.exists(), f"compiled runtime missing from delivery package: {bytecode}"

    assert not (output_dir / ".git").exists()
    assert not (app_root / "tests").exists()
    assert (app_root / "drl-or-s" / "model" / "Military_mininet" / "agent0.pth").exists()
