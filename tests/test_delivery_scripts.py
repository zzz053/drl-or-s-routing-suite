import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_test_topology_controller_ports_match_military_topology():
    module = load_module("start_controllers_test", "start_controllers_test.py")

    assert module.TEST_CONTROLLER_PORTS == [6654, 6655, 6656, 6657, 6658, 6659, 6670]


def test_test_topology_manager_defaults_to_no_external_terminal():
    module = load_module("start_controllers_test", "start_controllers_test.py")
    manager = module.build_manager()

    assert manager.use_terminal is False
    assert manager.pid_file.as_posix() == "/tmp/ryu_controllers_test.pid"
    assert manager.log_dir == ROOT / "logs"
    assert manager.controller_log_path(6654) == ROOT / "logs" / "ryu_controller_6654.log"


def test_start_suite_launches_military_topology():
    text = (ROOT / "start_suite.sh").read_text(encoding="utf-8")

    assert '"$PYTHON_BIN" -u start_controllers_test.py start -n' in text
    assert "testbed/creat_test_topo.py" in text
    assert "sudo" in text


def test_start_suite_allows_runtime_python_and_route_mode_overrides():
    text = (ROOT / "start_suite.sh").read_text(encoding="utf-8")

    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' in text
    assert 'SERVER_AGENT_ROUTE_MODE="${SERVER_AGENT_ROUTE_MODE:-hybrid}"' in text
    assert '"$PATH_SERVICE_PYTHON" drl-or-s/path_service.py' in text
    assert '"$PYTHON_BIN" server_agent.py "$SERVER_AGENT_ROUTE_MODE"' in text


def test_start_suite_supports_optional_external_interface():
    text = (ROOT / "start_suite.sh").read_text(encoding="utf-8")

    assert 'EXTERNAL_INTF="${1:-}"' in text
    assert 'EXTERNAL_LINK_PORTS="${EXTERNAL_LINK_PORTS:-1:20}"' in text
    assert 'sudo "$PYTHON_BIN" testbed/creat_test_topo.py "$EXTERNAL_INTF"' in text


def test_topology_installs_real_subnet_routes_via_virtual_gateway():
    text = (ROOT / "testbed" / "creat_test_topo.py").read_text(encoding="utf-8")

    assert "configure_hybrid_host_routes(" in text
    assert "HYBRID_GATEWAY_IP" in text
    assert "HYBRID_REAL_ROUTES" in text


def test_drl_assets_are_packaged_for_standalone_delivery():
    model_dir = ROOT / "drl-or-s" / "model" / "Military_mininet"
    topology_dir = ROOT / "drl-or-s" / "topology" / "Military"
    simenv_text = (ROOT / "drl-or-s" / "net_env" / "simenv.py").read_text(encoding="utf-8")
    path_service_text = (ROOT / "drl-or-s" / "path_service.py").read_text(encoding="utf-8")

    assert len(list(model_dir.glob("agent*.pth"))) == 47
    assert (model_dir / "agent0.pth").exists()
    assert (model_dir / "agent46.pth").exists()
    assert (topology_dir / "Topology.txt").exists()
    assert (topology_dir / "TM.txt").exists()
    assert "os.path.join(package_root, \"topology\", toponame)" in simenv_text
    assert "os.path.join(SERVICE_DIR, \"model\", \"Military_mininet\")" in path_service_text


def test_standalone_metadata_is_packaged():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    readme = (ROOT / "README_DELIVERY.md").read_text(encoding="utf-8")

    for package in ["flask", "flask-cors", "networkx", "ryu", "torch", "torch-geometric"]:
        assert package in requirements
    assert "__pycache__/" in gitignore
    assert "logs/" in gitignore
    assert "self-contained" in readme
