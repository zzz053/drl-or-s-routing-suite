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
