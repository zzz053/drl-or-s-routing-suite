"""Build lightweight Web acceptance status without data-plane side effects."""

from tools.acceptance_config import AcceptanceConfigError, load_acceptance_config


def _safe_count(value):
    try:
        return len(value() if callable(value) else value)
    except TypeError:
        return 0


def _graph_counts(graph):
    if graph is None:
        return 0, 0
    nodes = getattr(graph, "nodes", [])
    edges = getattr(graph, "edges", [])
    return _safe_count(nodes), _safe_count(edges)


def _iter_route_sessions(route_store):
    for items in (route_store or {}).values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield item


def _find_recent_route_session(route_store, virtual_ip, real_ip):
    matches = [
        item for item in _iter_route_sessions(route_store)
        if item.get("src_ip") == virtual_ip and item.get("dst_ip") == real_ip
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: (item.get("updated_at") or 0, item.get("created_at") or 0), reverse=True)
    latest = dict(matches[0])
    return {
        "src_ip": latest.get("src_ip"),
        "dst_ip": latest.get("dst_ip"),
        "path_source": latest.get("path_source", latest.get("decision_source", "unknown")),
        "updated_at": latest.get("updated_at", latest.get("created_at", 0)),
        "switch_path": latest.get("switch_path", []),
    }


def build_acceptance_status(server_agent, config_path="config/hybrid_acceptance.json"):
    try:
        config = load_acceptance_config(config_path)
    except AcceptanceConfigError as exc:
        return {
            "status": "unknown",
            "issues": [str(exc)],
            "recent_route_session": None,
        }

    validation = config["validation"]
    hybrid = config["hybrid"]
    controllers_expected = len(config["controllers"]["ports"])
    controllers_connected = len(getattr(server_agent, "clients", {}) or {})
    drl_connected = getattr(server_agent, "path_service_sock", None) is not None
    graph_nodes, graph_edges = _graph_counts(getattr(server_agent, "G", None))
    recent = _find_recent_route_session(
        getattr(server_agent, "controller_route_sessions", {}),
        validation["virtual_host_ip"],
        validation["real_host_ip"],
    )

    issues = []
    if controllers_connected < controllers_expected:
        issues.append(f"controllers connected {controllers_connected}/{controllers_expected}")
    if not drl_connected:
        issues.append("DRL path_service not connected")
    if graph_nodes == 0:
        issues.append("topology graph is empty")

    if issues:
        status = "not_ready"
    elif recent:
        status = "ready"
    else:
        status = "partial"
        issues.append("no recent route session for virtual-to-real validation pair")

    return {
        "status": status,
        "virtual_host_name": validation["virtual_host_name"],
        "virtual_host_ip": validation["virtual_host_ip"],
        "real_host_ip": validation["real_host_ip"],
        "controllers_expected": controllers_expected,
        "controllers_connected": controllers_connected,
        "drl_connected": drl_connected,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "hybrid_gateway_ip": hybrid["gateway_ip"],
        "real_routes": hybrid["real_routes"],
        "recent_route_session": recent,
        "issues": issues,
    }
