#!/usr/bin/env python3
"""Health checks for hybrid Mininet/real-switch acceptance runs."""

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.acceptance_config import AcceptanceConfigError, build_runtime_env, load_acceptance_config
from tools.acceptance_feature_audit import audit_features
from tools.web_consistency_audit import audit_payloads


LOG_DIR = Path("logs")
WEB_BASE_URL = "http://127.0.0.1:6009"
SEVERE_LOG_PATTERNS = (
    "Traceback",
    "AttributeError",
    "root_disconnected",
    "local variable",
    "barrier",
)


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: str = ""

    def to_dict(self):
        return asdict(self)


def classify_health(control_checks, data_checks):
    if any(item.status == "fail" for item in control_checks):
        return "fail"
    if any(item.status in ("fail", "risk") for item in data_checks):
        return "risk"
    return "pass"


def command_contains_route(output, cidr, gateway_ip):
    for line in (output or "").splitlines():
        if cidr in line and gateway_ip in line and "via" in line:
            return True
    return False


def parse_ping_statistics(output):
    stats = {}
    packet_match = re.search(
        r"(?P<tx>\d+)\s+packets transmitted,\s+(?P<rx>\d+)\s+(?:packets )?received,\s+"
        r"(?P<loss>[\d.]+)%\s+packet loss",
        output or "",
    )
    if packet_match:
        stats["transmitted"] = int(packet_match.group("tx"))
        stats["received"] = int(packet_match.group("rx"))
        stats["loss_percent"] = float(packet_match.group("loss"))

    rtt_match = re.search(
        r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
        r"(?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+)/(?P<mdev>[\d.]+) ms",
        output or "",
    )
    if rtt_match:
        stats["min_rtt_ms"] = float(rtt_match.group("min"))
        stats["avg_rtt_ms"] = float(rtt_match.group("avg"))
        stats["max_rtt_ms"] = float(rtt_match.group("max"))
        stats["mdev_rtt_ms"] = float(rtt_match.group("mdev"))
        stats["estimated_one_way_ms"] = stats["avg_rtt_ms"] / 2.0
    return stats


def flow_output_has_bidirectional_flows(outputs, virtual_ip, real_ip):
    combined = "\n".join(outputs or [])
    forward = (
        f"nw_src={virtual_ip}" in combined
        and f"nw_dst={real_ip}" in combined
    )
    reverse = (
        f"nw_src={real_ip}" in combined
        and f"nw_dst={virtual_ip}" in combined
    )
    return forward and reverse


def switch_names_for_flow_checks(config, route_sessions=None, virtual_switch_dpid_max=1000):
    dpids = set()
    for item in (config.get("hybrid", {}) or {}).get("external_link_ports", []):
        try:
            dpid = int(item.get("dpid"))
        except (TypeError, ValueError):
            continue
        if 0 < dpid <= virtual_switch_dpid_max:
            dpids.add(dpid)

    validation = config.get("validation", {}) or {}
    expected_virtual = validation.get("expected_virtual_switch_dpid")
    if expected_virtual is not None:
        try:
            dpid = int(expected_virtual)
        except (TypeError, ValueError):
            dpid = None
        if dpid and 0 < dpid <= virtual_switch_dpid_max:
            dpids.add(dpid)

    for session in (route_sessions or {}).get("sessions", []) or []:
        for node in session.get("switch_path", []) or []:
            try:
                dpid = int(node)
            except (TypeError, ValueError):
                continue
            if 0 < dpid <= virtual_switch_dpid_max:
                dpids.add(dpid)

    return [f"s{dpid}" for dpid in sorted(dpids)]


def _expected_external_bridge_ports(config, virtual_switch_dpid_max=1000):
    expected = {}
    for item in (config.get("hybrid", {}) or {}).get("external_link_ports", []):
        try:
            dpid = int(item.get("dpid"))
            port = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        if 0 < dpid <= virtual_switch_dpid_max:
            expected[f"s{dpid}"] = port
    return expected


def run_command(command, timeout=8):
    input_text = None
    if command and command[0] == "sudo" and os.environ.get("SUDO_PASSWORD"):
        command = ["sudo", "-S", "-p", ""] + list(command[1:])
        input_text = os.environ["SUDO_PASSWORD"] + "\n"
    try:
        completed = subprocess.run(
            command,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or ""
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or f"timeout after {timeout}s"


def tcp_port_listening(port, host="127.0.0.1", timeout=0.3):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def check_required_ports(config):
    checks = []
    required_ports = [6001, 6009, 8889] + list(config["controllers"]["ports"])
    for port in required_ports:
        if tcp_port_listening(port):
            checks.append(CheckResult(f"port_{port}", "pass", f"端口 {port} 正在监听"))
        else:
            checks.append(CheckResult(f"port_{port}", "fail", f"端口 {port} 未监听"))
    return checks


def check_web_apis():
    checks = []
    for path in (
        "/api/health",
        "/api/statistics",
        "/api/acceptance/status",
        "/api/controllers",
        "/api/topo",
        "/api/graph?include_flows=0",
        "/api/route_sessions",
    ):
        url = f"{WEB_BASE_URL}{path}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    checks.append(CheckResult(f"web_{path}", "pass", f"{path} 可访问"))
                else:
                    checks.append(CheckResult(f"web_{path}", "fail", f"{path} 返回 HTTP {resp.status}"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            checks.append(CheckResult(f"web_{path}", "fail", f"{path} 不可访问", str(exc)))
    return checks


def fetch_web_json(path):
    url = f"{WEB_BASE_URL}{path}"
    with urllib.request.urlopen(url, timeout=2) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_web_consistency():
    try:
        graph = fetch_web_json("/api/graph?include_flows=0")
        sessions = fetch_web_json("/api/route_sessions")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [CheckResult("web_consistency", "fail", "Web 拓扑一致性审计不可用", str(exc))]

    errors = audit_payloads(graph, sessions)
    if errors:
        return [CheckResult("web_consistency", "fail", "Web 拓扑和路径会话不一致", "\n".join(errors[:20]))]
    details = (
        f"nodes={len(graph.get('nodes') or [])}, "
        f"edges={len(graph.get('edges') or [])}, "
        f"sessions={len(sessions.get('sessions') or [])}"
    )
    return [CheckResult("web_consistency", "pass", "Web 拓扑和路径会话一致", details)]


def _edge_endpoint(edge, *names):
    for name in names:
        if name in edge:
            return edge.get(name)
    return None


def _edge_src_port(edge):
    data = edge.get("data") if isinstance(edge.get("data"), dict) else edge
    return data.get("src_port")


def check_static_hybrid_links(config):
    links = (config.get("hybrid", {}) or {}).get("static_links", []) or []
    if not links:
        return [CheckResult("static_hybrid_links", "pass", "未配置静态虚实链路")]
    try:
        graph = fetch_web_json("/api/graph?include_flows=0")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [CheckResult("static_hybrid_links", "fail", "静态虚实链路检查不可用", str(exc))]

    edges = graph.get("edges") or []
    missing = []

    def has_edge(src, dst, port):
        for edge in edges:
            edge_src = _edge_endpoint(edge, "source", "from", "src")
            edge_dst = _edge_endpoint(edge, "target", "to", "dst")
            try:
                if int(edge_src) != int(src) or int(edge_dst) != int(dst):
                    continue
            except (TypeError, ValueError):
                continue
            try:
                return int(_edge_src_port(edge)) == int(port)
            except (TypeError, ValueError):
                return False
        return False

    for link in links:
        src_dpid = link["src_dpid"]
        src_port = link["src_port"]
        dst_dpid = link["dst_dpid"]
        dst_port = link["dst_port"]
        if not has_edge(src_dpid, dst_dpid, src_port):
            missing.append(f"{src_dpid}:{src_port} -> {dst_dpid}")
        if not has_edge(dst_dpid, src_dpid, dst_port):
            missing.append(f"{dst_dpid}:{dst_port} -> {src_dpid}")

    if missing:
        return [CheckResult("static_hybrid_links", "fail", "静态虚实链路与运行拓扑不一致", "\n".join(missing))]
    return [CheckResult("static_hybrid_links", "pass", "静态虚实链路已进入运行拓扑")]


def check_recent_logs(log_dir=LOG_DIR, max_lines=400):
    severe = []
    if not log_dir.exists():
        return [CheckResult("recent_logs", "risk", "logs 目录不存在")]
    for path in sorted(log_dir.glob("*.log")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        except OSError as exc:
            severe.append(f"{path}: {exc}")
            continue
        for line in lines:
            if any(pattern in line for pattern in SEVERE_LOG_PATTERNS):
                severe.append(f"{path.name}: {line}")
    if severe:
        return [CheckResult("recent_logs", "fail", "最近日志存在严重错误", "\n".join(severe[:20]))]
    return [CheckResult("recent_logs", "pass", "最近日志未发现严重错误")]


def check_feature_audit(feature_audit):
    status = feature_audit.get("status", "fail")
    failed = [
        item.get("name", "<unknown>")
        for item in feature_audit.get("features", [])
        if item.get("status") != "pass" and item.get("required", True)
    ]
    if status == "pass":
        return [CheckResult("feature_audit", "pass", "项目核心功能覆盖审计通过")]
    return [CheckResult("feature_audit", "fail", "项目核心功能覆盖审计失败", "\n".join(failed))]


def _find_process_pid(ps_output, needle):
    for line in (ps_output or "").splitlines():
        stripped = line.strip()
        try:
            pid, args = stripped.split(None, 1)
        except ValueError:
            continue
        if pid.isdigit() and needle in args:
            return pid
    return None


def _parse_proc_environ(output):
    env = {}
    for item in (output or "").split("\0"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        env[key] = value
    return env


def check_runtime_environment(config, runner=run_command):
    expected_env = build_runtime_env(config)
    keys = [
        "SERVER_AGENT_ROUTE_MODE",
        "DRL_ROUTE_MODE",
        "DRL_K_CANDIDATES",
        "DRL_INFERENCE_TIMEOUT_MS",
        "DRL_MIN_CONFIDENCE",
        "ROUTE_FLOW_IDLE_TIMEOUT",
        "ROUTE_FLOW_HARD_TIMEOUT",
        "FLOW_INSTALL_BARRIER_TIMEOUT",
        "EXTERNAL_LINK_PORTS",
        "EXTERNAL_SWITCH",
        "EXTERNAL_PORT",
        "EXTERNAL_ARP_ALLOWED_PREFIXES",
        "VIRTUAL_SWITCH_DPID_MAX",
        "EXTERNAL_LINK_METRICS_JSON",
        "STATIC_HYBRID_LINKS_JSON",
        "TRAFFIC_CLASSES_JSON",
    ]

    code, output = runner(["ps", "-eo", "pid=,args="], timeout=5)
    if code != 0:
        return [CheckResult("runtime_environment", "risk", "无法读取运行进程列表", output)]
    pid = _find_process_pid(output, "server_agent.py")
    if not pid:
        return [CheckResult("runtime_environment", "risk", "未找到 server_agent.py 进程", output)]
    code, env_output = runner(["cat", f"/proc/{pid}/environ"], timeout=5)
    if code != 0:
        return [CheckResult("runtime_environment", "risk", f"无法读取 server_agent.py 进程 {pid} 的环境变量", env_output)]
    actual = _parse_proc_environ(env_output)
    mismatches = []
    for key in keys:
        expected = str(expected_env.get(key, ""))
        observed = actual.get(key)
        if observed != expected:
            mismatches.append(f"{key}: expected={expected!r}, actual={observed!r}")
    if mismatches:
        return [CheckResult("runtime_environment", "fail", "运行进程环境变量与 JSON 配置不一致", "\n".join(mismatches))]
    return [CheckResult("runtime_environment", "pass", "运行进程环境变量与 JSON 配置一致")]


def check_external_interface(config, runner=run_command):
    checks = []
    intf = config.get("external_interface", "")
    expected = _expected_external_bridge_ports(config)
    expected_bridges = sorted(expected)

    code, output = runner(["ip", "link", "show", intf], timeout=5)
    if code != 0:
        return [CheckResult("external_interface_exists", "fail", f"配置外部网卡 {intf} 不存在", output)]
    checks.append(CheckResult("external_interface_exists", "pass", f"配置外部网卡 {intf} 存在"))

    code, output = runner(["ip", "route", "show", "default"], timeout=5)
    if code == 0 and any(f" dev {intf}" in line for line in output.splitlines()):
        checks.append(CheckResult(
            "external_interface_default_route",
            "fail",
            f"配置外部网卡 {intf} 承载默认路由，不能作为数据面网卡",
            output,
        ))
    else:
        checks.append(CheckResult("external_interface_default_route", "pass", f"配置外部网卡 {intf} 未承载默认路由", output))

    code, output = runner(["sudo", "ovs-vsctl", "port-to-br", intf], timeout=5)
    if code != 0:
        return checks + [CheckResult(
            "external_interface_ovs_bridge",
            "fail",
            f"配置外部网卡 {intf} 未加入预期 OVS 边界 {', '.join(expected_bridges) or '-'}",
            output,
        )]
    bridge = output.strip()
    if bridge not in expected:
        return checks + [CheckResult(
            "external_interface_ovs_bridge",
            "fail",
            f"配置外部网卡 {intf} 接入 {bridge}，但配置边界是 {', '.join(expected_bridges) or '-'}",
            output,
        )]
    checks.append(CheckResult("external_interface_ovs_bridge", "pass", f"配置外部网卡 {intf} 已接入 {bridge}"))

    code, output = runner(["sudo", "ovs-vsctl", "get", "Interface", intf, "ofport"], timeout=5)
    if code != 0:
        checks.append(CheckResult("external_interface_ofport", "fail", f"无法读取 {intf} 的 OpenFlow 端口", output))
        return checks
    try:
        actual_port = int(output.strip())
    except ValueError:
        checks.append(CheckResult("external_interface_ofport", "fail", f"{intf} 的 OpenFlow 端口不是数字", output))
        return checks
    expected_port = expected[bridge]
    if actual_port != expected_port:
        checks.append(CheckResult(
            "external_interface_ofport",
            "fail",
            f"配置外部网卡 {intf} ofport={actual_port}，预期 {bridge}:port{expected_port}",
            output,
        ))
    else:
        checks.append(CheckResult("external_interface_ofport", "pass", f"配置外部网卡 {intf} 已固定为 {bridge}:port{expected_port}"))
    return checks


def _find_mininet_host_pid(host_name, runner=run_command):
    code, output = runner(["ps", "-eo", "pid=,args="], timeout=3)
    if code != 0:
        return None, output
    for line in output.splitlines():
        stripped = line.strip()
        try:
            pid, args = stripped.split(None, 1)
        except ValueError:
            continue
        if args == f"bash --norc --noediting -is mininet:{host_name}" and pid.isdigit():
            return pid, output
    return None, output


def check_data_plane(config, runner=run_command):
    validation = config["validation"]
    hybrid = config["hybrid"]
    host_name = validation["virtual_host_name"]
    virtual_ip = validation["virtual_host_ip"]
    real_ip = validation["real_host_ip"]
    gateway_ip = hybrid["gateway_ip"]
    real_routes = hybrid["real_routes"]
    checks = []

    pid, pid_output = _find_mininet_host_pid(host_name, runner=runner)
    if not pid:
        return [
            CheckResult(
                f"mininet_{host_name}",
                "risk",
                f"未找到 Mininet 主机 {host_name} 的进程",
                pid_output,
            )
        ]
    checks.append(CheckResult(f"mininet_{host_name}", "pass", f"找到 {host_name} 进程 {pid}"))

    route_code, route_output = runner(["sudo", "mnexec", "-a", pid, "ip", "route"], timeout=5)
    if route_code != 0:
        checks.append(CheckResult("host_route", "risk", f"{host_name} 路由检查不可用", route_output))
    else:
        missing = [
            cidr
            for cidr in real_routes
            if not command_contains_route(route_output, cidr, gateway_ip)
        ]
        if missing:
            checks.append(CheckResult("host_route", "fail", "真实网段路由缺失", route_output))
        else:
            checks.append(CheckResult("host_route", "pass", "真实网段路由存在", route_output))

    verification_stats = {}
    for label in ("warmup_ping", "verification_ping"):
        code, output = runner(
            ["sudo", "mnexec", "-a", pid, "ping", "-c", "3", "-W", "1", real_ip],
            timeout=8,
        )
        status = "pass" if code == 0 else "risk"
        message = f"{label} {'通过' if code == 0 else '未通过'}"
        checks.append(CheckResult(label, status, message, output))
        if label == "verification_ping":
            verification_stats = parse_ping_statistics(output)

    if verification_stats.get("received", 0) > 0 and "avg_rtt_ms" in verification_stats:
        details = (
            f"loss_percent={verification_stats.get('loss_percent', 0.0):.3f}, "
            f"min_rtt_ms={verification_stats['min_rtt_ms']:.3f}, "
            f"avg_rtt_ms={verification_stats['avg_rtt_ms']:.3f}, "
            f"max_rtt_ms={verification_stats['max_rtt_ms']:.3f}, "
            f"mdev_rtt_ms={verification_stats['mdev_rtt_ms']:.3f}, "
            f"estimated_one_way_ms={verification_stats['estimated_one_way_ms']:.3f}"
        )
        checks.append(CheckResult("virtual_real_latency", "pass", "虚实端到端延迟已主动测量", details))
    else:
        checks.append(CheckResult(
            "virtual_real_latency",
            "risk",
            "未能从 verification ping 解析虚实端到端延迟",
            json.dumps(verification_stats, ensure_ascii=False),
        ))

    try:
        route_sessions = fetch_web_json("/api/route_sessions")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        route_sessions = {}

    flow_outputs = []
    flow_failed = False
    switch_names = switch_names_for_flow_checks(config, route_sessions=route_sessions)
    if not switch_names:
        checks.append(CheckResult("ovs_flows", "risk", "未能推导需要检查的 OVS 交换机"))
        return checks

    for switch in switch_names:
        code, output = runner(["sudo", "ovs-ofctl", "dump-flows", switch], timeout=5)
        flow_outputs.append(output)
        if code != 0:
            flow_failed = True
    if flow_failed:
        checks.append(CheckResult("ovs_flows", "risk", "OVS 流表检查不可用", "\n".join(flow_outputs)))
    elif flow_output_has_bidirectional_flows(flow_outputs, virtual_ip=virtual_ip, real_ip=real_ip):
        checks.append(CheckResult("ovs_flows", "pass", f"{'/'.join(switch_names)} 存在虚实双向流表", "\n".join(flow_outputs)))
    else:
        checks.append(CheckResult("ovs_flows", "risk", "未发现完整虚实双向流表", "\n".join(flow_outputs)))
    return checks


def run_health(config_path="config/hybrid_acceptance.json"):
    try:
        config = load_acceptance_config(config_path)
    except AcceptanceConfigError as exc:
        return {
            "status": "fail",
            "config_error": str(exc),
            "config": None,
            "control_checks": [CheckResult("config", "fail", str(exc)).to_dict()],
            "data_checks": [],
        }

    feature_audit = audit_features()
    control_checks = []
    control_checks.extend(check_feature_audit(feature_audit))
    control_checks.extend(check_runtime_environment(config))
    control_checks.extend(check_external_interface(config))
    control_checks.extend(check_required_ports(config))
    control_checks.extend(check_web_apis())
    control_checks.extend(check_web_consistency())
    control_checks.extend(check_static_hybrid_links(config))
    control_checks.extend(check_recent_logs())
    data_checks = check_data_plane(config)
    status = classify_health(control_checks, data_checks)
    return {
        "status": status,
        "config": config,
        "feature_audit": feature_audit,
        "control_checks": [item.to_dict() for item in control_checks],
        "data_checks": [item.to_dict() for item in data_checks],
    }


def _status_label(status):
    return {"pass": "通过", "risk": "有风险", "fail": "失败"}.get(status, status)


def print_summary(result):
    print(f"验收健康检查：{_status_label(result['status'])}")
    for section in ("control_checks", "data_checks"):
        for item in result.get(section, []):
            print(f"[{_status_label(item['status'])}] {item['name']}: {item['message']}")
            if item.get("details") and item["status"] != "pass":
                print(item["details"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run hybrid acceptance health checks.")
    parser.add_argument("--config", default="config/hybrid_acceptance.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = run_health(args.config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)
    return {"pass": 0, "fail": 1, "risk": 2}.get(result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
