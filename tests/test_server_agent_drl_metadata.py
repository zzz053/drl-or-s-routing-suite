import sys
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_agent import ServerAgent


def test_normalize_drl_decision_preserves_metadata():
    agent = ServerAgent.__new__(ServerAgent)
    graph = nx.DiGraph()
    graph.add_edge("10.0.0.1", 1)
    graph.add_edge(1, 2)
    graph.add_edge(2, "10.0.0.2")
    agent.G = graph

    raw_response = {
        "path": [1, 2],
        "decision_source": "drl_model",
        "model_used": True,
        "fallback_reason": None,
        "confidence": 0.9,
        "compute_time": 0.02,
    }

    result = agent._normalize_drl_decision(raw_response, "10.0.0.1", "10.0.0.2")

    assert result["path"] == ["10.0.0.1", 1, 2, "10.0.0.2"]
    assert result["decision_source"] == "drl_model"
    assert result["model_used"] is True
    assert result["fallback_reason"] is None
    assert result["confidence"] == 0.9
    assert result["compute_time"] == 0.02


def test_build_path_service_request_includes_drl_service_metadata():
    agent = ServerAgent.__new__(ServerAgent)
    graph = nx.DiGraph()
    graph.add_edge("10.0.0.1", 1, edge_type="host_link")
    graph.add_edge(1, 2, edge_type="switch_link", delay=1, bw=10, loss=0)
    graph.add_edge(2, "10.0.0.2", edge_type="host_link")
    agent.G = graph
    agent.link_down_set = {}
    agent.route_mode = "hybrid"

    request = agent._build_path_service_request(
        {
            "route_mode": "hybrid",
            "route_policy": "max_bandwidth",
            "task_type": "task_1",
            "drl_type": 1,
            "drl_demand_kbps": 1500,
            "drl_duration": 100,
        },
        "10.0.0.1",
        "10.0.0.2",
        1,
        2,
    )

    assert request["task_type"] == "task_1"
    assert request["route_policy"] == "max_bandwidth"
    assert request["drl_type"] == 1
    assert request["drl_demand_kbps"] == 1500
    assert request["drl_duration"] == 100
    assert request["candidates"][0]["switch_path"] == [1, 2]
