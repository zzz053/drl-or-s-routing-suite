#!/usr/bin/env python3
"""Static feature coverage audit for acceptance readiness.

The health check proves a running environment. This audit proves that the
deliverable still contains the project capabilities that acceptance depends on.
It is intentionally static and dependency-light so it can run before Mininet or
real switches are available.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]

CYTHON_PROTECTED_FILES = {
    "common_config.py",
    "controller.py",
    "controller_helpers.py",
    "external_host_guard.py",
    "host_model.py",
    "hybrid_gateway.py",
    "network_metrics.py",
    "packetin_arp.py",
    "packetin_ip.py",
    "packetin_lldp.py",
    "routing_policy.py",
    "server_agent.py",
    "server_message_handlers.py",
    "server_path_service.py",
    "web_api.py",
    "web_ui_html.py",
    "drl-or-s/path_service.py",
}

CYTHON_PROTECTED_PREFIXES = (
    "drl-or-s/a2c_ppo_acktr/",
    "drl-or-s/net_env/",
)

CYTHON_MODULE_OVERRIDES = {
    "controller.py": "controller_core",
    "server_agent.py": "server_agent_core",
    "drl-or-s/path_service.py": "path_service_core",
}


@dataclass
class FeatureCheck:
    name: str
    status: str
    required: bool
    files: list[str]
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


FEATURES = [
    {
        "name": "lifecycle_start_stop_health_report",
        "files": {
            "acceptance.sh": ["start)", "stop)", "health)", "report)"],
        },
        "message": "一键启动、停止、健康检查和报告入口存在",
    },
    {
        "name": "static_hybrid_boundary_config",
        "files": {
            "tools/acceptance_config.py": ["EXTERNAL_LINK_PORTS", "STATIC_HYBRID_LINKS_JSON", "TRAFFIC_CLASSES_JSON", "HYBRID_REAL_ROUTES"],
            "config/hybrid_acceptance.json": ["external_link_ports", "static_links", "traffic_classes", "real_routes"],
        },
        "message": "静态虚实边界配置和环境变量生成能力存在",
    },
    {
        "name": "web_acceptance_status",
        "files": {
            "web_api.py": ["/api/acceptance/status", "build_acceptance_status"],
            "tools/acceptance_web_status.py": ["build_acceptance_status"],
        },
        "message": "Web 验收状态 API 存在，首页不再渲染验收状态卡片",
    },
    {
        "name": "web_route_session_highlight",
        "files": {
            "web_ui_html.py": ["applyRouteSessionHighlight", "lastHighlightedNodeIds", "lastHighlightedEdgeIds"],
        },
        "message": "Web 路径会话点击高亮能力存在",
    },
    {
        "name": "web_route_replan_selection_rebind",
        "files": {
            "web_ui_html.py": ["selectedRouteSessionSignature", "getRouteSessionSignature", "const matched = routeSessions.find"],
        },
        "message": "路径重规划后按会话签名重新关联选中路径的逻辑存在",
    },
    {
        "name": "web_manual_flow_operations",
        "files": {
            "web_api.py": ["@app.route('/api/flows', methods=['POST'])", "@app.route('/api/flows', methods=['DELETE'])"],
            "server_agent.py": ["def add_manual_flow", "def delete_manual_flow", "manual_flow_mod"],
            "web_ui_html.py": ["handleAddFlowSubmit", "deleteFlow"],
        },
        "message": "Web 手动流表下发和删除能力存在",
    },
    {
        "name": "web_slim_graph_refresh",
        "files": {
            "web_api.py": ["include_flows", "_prepare_node_data_for_graph"],
            "web_ui_html.py": ["fetch('/api/graph?include_flows=0')", "syncDataSet", "computeTopologySignature"],
        },
        "message": "Web 瘦图刷新、增量同步和拓扑签名逻辑存在",
    },
    {
        "name": "web_consistency_audit",
        "files": {
            "tools/web_consistency_audit.py": ["audit_payloads", "/api/graph?include_flows=0", "/api/route_sessions"],
        },
        "message": "Web 拓扑和路径会话一致性审计工具存在",
    },
    {
        "name": "drl_route_modes",
        "files": {
            "common_config.py": ["DRL_ROUTE_MODE", "shadow", "hybrid", "drl", "spf"],
            "server_agent.py": ["VALID_DRL_ROUTE_MODES", "_choose_final_path_response"],
        },
        "message": "SPF、shadow、hybrid、DRL 路由模式存在",
    },
    {
        "name": "k_shortest_candidates",
        "files": {
            "server_path_service.py": ["build_k_shortest_candidates", "shortest_simple_paths", "summarize_path_metrics"],
            "server_agent.py": ["DRL_K_CANDIDATES", "candidates"],
        },
        "message": "K 候选路径生成和 DRL 请求携带候选路径能力存在",
    },
    {
        "name": "policy_weighted_routing",
        "files": {
            "routing_policy.py": ["min_delay", "max_bandwidth", "min_loss", "hybrid"],
            "server_path_service.py": ["compute_edge_weight", "route_policy"],
        },
        "message": "策略化路径权重计算能力存在",
    },
    {
        "name": "flow_lifecycle_cleanup",
        "files": {
            "common_config.py": ["ROUTE_FLOW_IDLE_TIMEOUT", "ROUTE_FLOW_HARD_TIMEOUT"],
            "controller.py": ["_flow_removed_handler", "_remove_flow_from_sessions"],
            "server_agent.py": ["handle_flow_removed", "mark_route_sessions_dirty"],
        },
        "message": "自动路径流表生命周期和会话清理逻辑存在",
    },
    {
        "name": "link_down_reroute",
        "files": {
            "controller.py": ["_invalidate_sessions_on_link_failure", "_reroute_session"],
            "server_agent.py": ["handle_link_down", "link_down_set", "stale_link_down_ignored"],
        },
        "message": "链路 down 标记、受影响会话失效和重规划逻辑存在",
    },
    {
        "name": "hybrid_gateway_proxy_arp",
        "files": {
            "hybrid_gateway.py": ["is_gateway_arp_request", "parse_hybrid_real_routes"],
            "packetin_arp.py": ["HYBRID_GATEWAY_IP", "HYBRID_GATEWAY_MAC"],
        },
        "message": "虚拟网关 proxy ARP 和真实网段路由能力存在",
    },
    {
        "name": "external_host_guard",
        "files": {
            "external_host_guard.py": ["should_skip_external_host_learning", "should_drop_external_arp"],
            "controller.py": ["EXTERNAL_LINK_PORTS", "EXTERNAL_ARP_ALLOWED_PREFIXES"],
        },
        "message": "外部主机隔离和外部 ARP 保护逻辑存在",
    },
    {
        "name": "mininet_random_load_test",
        "files": {
            "tools/mininet_load_test.py": ["build_flow_specs", "iperf3", "ThreadPoolExecutor", "render_markdown_report"],
            "acceptance.sh": ["load)", "tools/mininet_load_test.py"],
        },
        "message": "Mininet 随机主机对 iperf3 负载测试工具和入口存在",
    },
]


def _read_text(path: str) -> str:
    return (ROOT_DIR / path).read_text(encoding="utf-8", errors="replace")


def _is_cython_delivery_runtime() -> bool:
    try:
        return (ROOT_DIR / "CYTHON_BUILD_MANIFEST.json").exists() or any(ROOT_DIR.glob("*.so"))
    except OSError:
        return False


def _is_cython_protected_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized in CYTHON_PROTECTED_FILES
        or any(normalized.startswith(prefix) for prefix in CYTHON_PROTECTED_PREFIXES)
    )


def _module_name_for_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized in CYTHON_MODULE_OVERRIDES:
        return CYTHON_MODULE_OVERRIDES[normalized]
    if normalized.startswith("drl-or-s/"):
        normalized = normalized[len("drl-or-s/"):]
    return normalized[:-3].replace("/", ".")


def _compiled_module_exists(path: str) -> bool:
    module_name = _module_name_for_path(path)
    module_path = Path(*module_name.split("."))
    search_root = ROOT_DIR
    if path.replace("\\", "/").startswith("drl-or-s/") and module_name == "path_service_core":
        search_root = ROOT_DIR / "drl-or-s"
        module_path = Path(module_name)
    candidates = list((search_root / module_path.parent).glob(f"{module_path.name}*.so"))
    candidates.extend((search_root / module_path.parent).glob(f"{module_path.name}*.pyd"))
    return any(candidate.is_file() for candidate in candidates)


def _missing_markers(path: str, markers: Iterable[str]) -> list[str]:
    if _is_cython_delivery_runtime() and _is_cython_protected_path(path):
        if _compiled_module_exists(path):
            return []
        return [f"<compiled module missing for protected path: {path}>"]
    try:
        text = _read_text(path)
    except OSError:
        return [f"<file missing: {path}>"]
    return [marker for marker in markers if marker not in text]


def classify_audit(features: list[dict]) -> str:
    if any(item.get("required", True) and item.get("status") != "pass" for item in features):
        return "fail"
    if any(item.get("status") != "pass" for item in features):
        return "risk"
    return "pass"


def audit_features() -> dict:
    results = []
    for spec in FEATURES:
        missing = {}
        for path, markers in spec["files"].items():
            missing_markers = _missing_markers(path, markers)
            if missing_markers:
                missing[path] = missing_markers
        status = "fail" if missing else "pass"
        message = spec["message"] if not missing else f"缺少功能标记: {missing}"
        results.append(FeatureCheck(
            name=spec["name"],
            status=status,
            required=spec.get("required", True),
            files=list(spec["files"].keys()),
            message=message,
        ).to_dict())
    return {
        "status": classify_audit(results),
        "features": results,
    }


def print_summary(result: dict) -> None:
    print(f"功能覆盖审计：{result['status']}")
    for item in result.get("features", []):
        print(f"[{item['status']}] {item['name']}: {item['message']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit acceptance feature coverage.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = audit_features()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)
    return 0 if result["status"] == "pass" else 2 if result["status"] == "risk" else 1


if __name__ == "__main__":
    raise SystemExit(main())
