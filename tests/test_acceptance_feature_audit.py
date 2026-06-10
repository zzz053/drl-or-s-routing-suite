import tools.acceptance_feature_audit as feature_audit
from tools.acceptance_feature_audit import audit_features, classify_audit


def test_acceptance_feature_audit_covers_core_project_capabilities():
    result = audit_features()
    names = {item["name"] for item in result["features"]}

    expected = {
        "lifecycle_start_stop_health_report",
        "static_hybrid_boundary_config",
        "web_acceptance_status",
        "web_route_session_highlight",
        "web_route_replan_selection_rebind",
        "web_manual_flow_operations",
        "web_slim_graph_refresh",
        "web_consistency_audit",
        "drl_route_modes",
        "k_shortest_candidates",
        "policy_weighted_routing",
        "flow_lifecycle_cleanup",
        "link_down_reroute",
        "hybrid_gateway_proxy_arp",
        "external_host_guard",
        "mininet_random_load_test",
    }

    assert expected.issubset(names)
    assert result["status"] == "pass"


def test_classify_audit_fails_when_required_feature_is_missing():
    features = [
        {"name": "present", "status": "pass", "required": True},
        {"name": "missing", "status": "fail", "required": True},
        {"name": "optional", "status": "fail", "required": False},
    ]

    assert classify_audit(features) == "fail"


def test_feature_audit_accepts_cython_protected_files(monkeypatch, tmp_path):
    (tmp_path / "CYTHON_BUILD_MANIFEST.json").write_text("{}", encoding="utf-8")
    (tmp_path / "server_agent_core.so").write_bytes(b"native-extension")
    (tmp_path / "acceptance.sh").write_text("start)\n", encoding="utf-8")

    monkeypatch.setattr(feature_audit, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(feature_audit, "FEATURES", [
        {
            "name": "protected_core_feature",
            "files": {
                "server_agent.py": ["class ServerAgent", "def add_manual_flow"],
                "acceptance.sh": ["start)"],
            },
            "message": "protected core exists and non-core source markers exist",
        },
    ])

    result = feature_audit.audit_features()

    assert result["status"] == "pass"
    assert result["features"][0]["status"] == "pass"
