import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH_SERVICE = ROOT / "drl-or-s" / "path_service.py"


def load_path_service_module():
    spec = importlib.util.spec_from_file_location("path_service_under_test", PATH_SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decision_metadata_helper_marks_model_path():
    module = load_path_service_module()

    result = module._decision("drl_model", [1, 2, 3], model_used=True, fallback_reason=None)

    assert result["path"] == [1, 2, 3]
    assert result["decision_source"] == "drl_model"
    assert result["model_used"] is True
    assert result["fallback_reason"] is None


def test_decision_metadata_helper_marks_fallback():
    module = load_path_service_module()

    result = module._decision("dijkstra", [1, 4], model_used=False, fallback_reason="out_of_drl_range")

    assert result["decision_source"] == "dijkstra"
    assert result["model_used"] is False
    assert result["fallback_reason"] == "out_of_drl_range"


def test_service_starts_in_dijkstra_only_mode_when_drl_runtime_unavailable(monkeypatch):
    module = load_path_service_module()
    monkeypatch.setattr(module, "torch", None)
    monkeypatch.setattr(module, "Data", None)
    monkeypatch.setattr(module, "NetEnv", None)
    monkeypatch.setattr(module, "Policy", None)
    monkeypatch.setattr(module, "NET_ENV_IMPORT_ERROR", ImportError("missing torch runtime"))

    service = module.DRLPathService(topo_name="Abi", port=9999)
    result = service.compute_path(1, 3, topo_edges=[(1, 2), (2, 3), (1, 4)])

    assert result["path"] == [1, 2, 3]
    assert result["decision_source"] == "dijkstra"
    assert result["model_used"] is False
    assert result["fallback_reason"] == "drl_runtime_unavailable"


def test_compute_path_with_drl_uses_request_service_type_demand_and_duration(monkeypatch):
    module = load_path_service_module()
    service = module.DRLPathService.__new__(module.DRLPathService)
    service.num_node = 4
    service.num_agent = 1
    service.agent_to_node = [0]
    service.edge_indexs = [[[0, 1], [1, 0]]]
    service.adj_masks = [[0, 1, 0, 0]]
    service.device = "cpu"
    service._last_model_action_used = False
    service.actor_critic = None

    captured = {}

    class FakeEnv:
        def calcSHR(self, src, dst):
            return [src, dst]

    service.env = FakeEnv()

    def fake_reset(src_node, dst_node, rtype=0, demand=100, duration=50):
        captured.update({
            "src_node": src_node,
            "dst_node": dst_node,
            "rtype": rtype,
            "demand": demand,
            "duration": duration,
        })
        return type("Req", (), {"rtype": rtype})(), []

    monkeypatch.setattr(service, "_reset_env_with_request", fake_reset)

    path = service.compute_path_with_drl(
        0,
        2,
        rtype=2,
        demand=1500,
        duration=100,
    )

    assert path == [0, 2]
    assert captured == {
        "src_node": 0,
        "dst_node": 2,
        "rtype": 2,
        "demand": 1500,
        "duration": 100,
    }
