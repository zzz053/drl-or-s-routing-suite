#!/usr/bin/env python3
"""Health checks for hybrid Mininet/real-switch acceptance runs."""

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.acceptance_config import AcceptanceConfigError, load_acceptance_config


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


def flow_output_has_bidirectional_flows(outputs, virtual_ip, real_ip):
    combined = "\n".join(outputs or [])
    forward = (
        "idle_timeout=120" in combined
        and f"nw_src={virtual_ip}" in combined
        and f"nw_dst={real_ip}" in combined
    )
    reverse = (
        "idle_timeout=120" in combined
        and f"nw_src={real_ip}" in combined
        and f"nw_dst={virtual_ip}" in combined
    )
    return forward and reverse


def run_command(command, timeout=8):
    input_text = None
    if command and command[0] == "sudo" and os.environ.get("SUDO_PASSWORD"):
        command = ["sudo", "-S"] + list(command[1:])
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

    for port in config["controllers"].get("forbidden_ports", []):
        if tcp_port_listening(port):
            checks.append(CheckResult(f"forbidden_port_{port}", "fail", f"禁用端口 {port} 正在监听"))
        else:
            checks.append(CheckResult(f"forbidden_port_{port}", "pass", f"禁用端口 {port} 未监听"))
    return checks


def check_web_apis():
    checks = []
    for path in ("/api/health", "/api/statistics", "/api/acceptance/status"):
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


def _find_mininet_host_pid(host_name, runner=run_command):
    code, output = runner(["ps", "-eo", "pid=,args="], timeout=3)
    if code != 0:
        return None, output
    for line in output.splitlines():
        stripped = line.strip()
        if f"bash --norc --noediting -is mininet:{host_name}" not in stripped:
            continue
        pid = stripped.split(None, 1)[0]
        if pid.isdigit():
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

    for label in ("warmup_ping", "verification_ping"):
        code, output = runner(
            ["sudo", "mnexec", "-a", pid, "ping", "-c", "3", "-W", "1", real_ip],
            timeout=8,
        )
        status = "pass" if code == 0 else "risk"
        message = f"{label} {'通过' if code == 0 else '未通过'}"
        checks.append(CheckResult(label, status, message, output))

    flow_outputs = []
    flow_failed = False
    for switch in ("s28", "s1"):
        code, output = runner(["sudo", "ovs-ofctl", "dump-flows", switch], timeout=5)
        flow_outputs.append(output)
        if code != 0:
            flow_failed = True
    if flow_failed:
        checks.append(CheckResult("ovs_flows", "risk", "OVS 流表检查不可用", "\n".join(flow_outputs)))
    elif flow_output_has_bidirectional_flows(flow_outputs, virtual_ip=virtual_ip, real_ip=real_ip):
        checks.append(CheckResult("ovs_flows", "pass", "s28/s1 存在虚实双向流表", "\n".join(flow_outputs)))
    else:
        checks.append(CheckResult("ovs_flows", "risk", "未发现完整虚实双向 idle_timeout=120 流表", "\n".join(flow_outputs)))
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

    control_checks = []
    control_checks.extend(check_required_ports(config))
    control_checks.extend(check_web_apis())
    control_checks.extend(check_recent_logs())
    data_checks = check_data_plane(config)
    status = classify_health(control_checks, data_checks)
    return {
        "status": status,
        "config": config,
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
