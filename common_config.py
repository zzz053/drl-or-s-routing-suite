"""
该文件从原始大文件中拆分了“配置常量”相关功能，集中管理控制器/根控通信参数、
Web 监听参数，以及按主机 TCP/UDP 端口区间划分任务类型的映射配置。

主要内容作用：
- SERVER_CONFIG：从控连接根控（server_agent）的地址与重连参数。
- CONTROLLER_IP / CONTROLLER_PORT / WEB_PORT：根控服务与 Web 接口监听参数。
- HOST_PORT_TASK_RANGES：按端口闭区间 [lo, hi] 映射业务类型；分类时先匹配目的端口，再匹配源端口。
- TASK_POLICY_MAP：任务类型映射到路由策略名。
- TASK_PRIORITY_MAP：任务类型映射到流表优先级。
"""

import os
import json


def _parse_external_link_ports(raw_value):
    ports = {}
    for item in (raw_value or "").split(","):
        item = item.strip()
        if not item:
            continue
        dpid_text, sep, port_text = item.partition(":")
        if not sep:
            continue
        try:
            dpid = int(dpid_text, 0)
            port = int(port_text, 0)
        except ValueError:
            continue
        ports.setdefault(dpid, set()).add(port)
    return ports


def _parse_external_link_metrics(raw_value):
    metrics = {}
    if not raw_value:
        return metrics
    try:
        items = json.loads(raw_value)
    except (TypeError, ValueError):
        return metrics
    if not isinstance(items, list):
        return metrics
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            dpid = int(item.get("dpid"))
            port = int(item.get("port"))
            delay_ms = float(item.get("delay_ms", 0.0))
            bandwidth_mbps = float(item.get("bandwidth_mbps", 1.0))
            loss_percent = float(item.get("loss_percent", 0.0))
        except (TypeError, ValueError):
            continue
        metrics[(dpid, port)] = {
            "delay_seconds": max(delay_ms, 0.0) / 1000.0,
            "bandwidth_mbps": max(bandwidth_mbps, 0.000001),
            "loss_percent": max(loss_percent, 0.0),
            "source": str(item.get("source", "configured")),
        }
    return metrics


def _parse_static_hybrid_links(raw_value):
    links = []
    if not raw_value:
        return links
    try:
        items = json.loads(raw_value)
    except (TypeError, ValueError):
        return links
    if not isinstance(items, list):
        return links
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            src_dpid = int(item.get("src_dpid"))
            src_port = int(item.get("src_port"))
            dst_dpid = int(item.get("dst_dpid"))
            dst_port = int(item.get("dst_port"))
            delay_ms = float(item.get("delay_ms", 0.0))
            bandwidth_mbps = float(item.get("bandwidth_mbps", 1.0))
            loss_percent = float(item.get("loss_percent", 0.0))
        except (TypeError, ValueError):
            continue
        links.append({
            "src_dpid": src_dpid,
            "src_port": src_port,
            "dst_dpid": dst_dpid,
            "dst_port": dst_port,
            "delay_seconds": max(delay_ms, 0.0) / 1000.0,
            "bandwidth_mbps": max(bandwidth_mbps, 0.000001),
            "loss_percent": max(loss_percent, 0.0),
            "source": str(item.get("source", "configured_static_link")),
        })
    return links

# 连接根控 server_agent（与 server_agent.py 中 CONTROLLER_IP/CONTROLLER_PORT 对应）
SERVER_CONFIG = {
    'server_ip': os.environ.get('SERVER_AGENT_IP', '127.0.0.1'),
    'server_port': int(os.environ.get('SERVER_AGENT_PORT', '6001')),
    'reconnect_interval': 5
}

# server_agent 本地监听配置
CONTROLLER_IP = os.environ.get('SERVER_AGENT_BIND_IP', '0.0.0.0')
CONTROLLER_PORT = int(os.environ.get('SERVER_AGENT_PORT', '6001'))
WEB_PORT = int(os.environ.get('WEB_PORT', '6009'))
PATH_SERVICE_HOST = os.environ.get('PATH_SERVICE_HOST', '127.0.0.1')
PATH_SERVICE_PORT = int(os.environ.get('PATH_SERVICE_PORT', '8889'))

# DRL 路由模式：
# - spf: 只使用 server_agent 本地 Dijkstra/最短路径
# - shadow: 旁路计算 DRL 建议，实际安装 fallback 路径
# - hybrid: 优先使用通过校验的 DRL 路径，失败回退 fallback
# - drl: 强制 DRL，仅用于实验
DRL_ROUTE_MODE = os.environ.get("DRL_ROUTE_MODE", "shadow").strip().lower()
if DRL_ROUTE_MODE not in {"spf", "shadow", "hybrid", "drl"}:
    DRL_ROUTE_MODE = "shadow"

DRL_K_CANDIDATES = int(os.environ.get("DRL_K_CANDIDATES", "5"))
DRL_INFERENCE_TIMEOUT_MS = int(os.environ.get("DRL_INFERENCE_TIMEOUT_MS", "100"))
DRL_MIN_CONFIDENCE = float(os.environ.get("DRL_MIN_CONFIDENCE", "0.50"))

# Controller-installed route flows expire after traffic stops, so flow_removed can
# clear controller/server/Web state. Manual Web flows still honor their explicit
# idle_timeout/hard_timeout values.
# Keep automatically installed route flows long enough for hybrid real/virtual
# experiments to stay warm between short validation runs. Operators can still
# lower this with ROUTE_FLOW_IDLE_TIMEOUT when they need faster cleanup.
ROUTE_FLOW_IDLE_TIMEOUT = int(os.environ.get("ROUTE_FLOW_IDLE_TIMEOUT", "120"))
ROUTE_FLOW_HARD_TIMEOUT = int(os.environ.get("ROUTE_FLOW_HARD_TIMEOUT", "0"))
FLOW_INSTALL_BARRIER_TIMEOUT = float(os.environ.get("FLOW_INSTALL_BARRIER_TIMEOUT", "0.5"))

# Comma-separated OpenFlow port whitelist for physical/real-network attachments.
# Example: EXTERNAL_LINK_PORTS=1:20 marks s1:port20 as a link/external port before LLDP learns it.
EXTERNAL_LINK_PORTS = _parse_external_link_ports(os.environ.get("EXTERNAL_LINK_PORTS", ""))
EXTERNAL_LINK_METRICS = _parse_external_link_metrics(os.environ.get("EXTERNAL_LINK_METRICS_JSON", ""))
STATIC_HYBRID_LINKS = _parse_static_hybrid_links(os.environ.get("STATIC_HYBRID_LINKS_JSON", ""))
EXTERNAL_ARP_ALLOWED_PREFIXES = [
    item.strip()
    for item in os.environ.get("EXTERNAL_ARP_ALLOWED_PREFIXES", "10.0.0.0/24").split(",")
    if item.strip()
]
VIRTUAL_SWITCH_DPID_MAX = int(os.environ.get("VIRTUAL_SWITCH_DPID_MAX", "1000"))
HYBRID_GATEWAY_IP = os.environ.get("HYBRID_GATEWAY_IP", "10.0.0.254")
HYBRID_GATEWAY_MAC = os.environ.get("HYBRID_GATEWAY_MAC", "02:00:00:00:fe:01")
HYBRID_REAL_ROUTES = os.environ.get("HYBRID_REAL_ROUTES", "192.168.103.0/24")

# 按主机 TCP/UDP 端口区间划分业务（闭区间）。
# 顺序有意义：对每个包先按目的端口查表，再按源端口；未命中则使用 default。
# 示例：1–5000 为业务 task_0，5001–10000 为 task_1，其余为 task_2（可按需增删改）。
HOST_PORT_TASK_RANGES = [
    (1, 5000, 'task_0'),
    (5001, 10000, 'task_1'),
    (10001, 65535, 'task_2'),
]

# 业务类型 -> 路由策略（先提供框架，默认沿用 shortest_path）
TASK_POLICY_MAP = {
    'task_0': 'shortest_path',
    'task_1': 'shortest_path',
    'task_2': 'shortest_path',
    'task_a': 'shortest_path',
    'task_b': 'shortest_path',
    'task_c': 'shortest_path',
    'default': 'shortest_path',
}

# 业务类型 -> 流表优先级（先提供框架）
TASK_PRIORITY_MAP = {
    'task_0': 30,
    'task_1': 20,
    'task_2': 10,
    'task_a': 30,
    'task_b': 20,
    'task_c': 10,
    'default': 1,
}
