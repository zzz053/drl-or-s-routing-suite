#!/usr/bin/env python3
"""Generate Markdown reports for hybrid acceptance checks."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.acceptance_health import run_health


STATUS_LABELS = {
    "pass": "通过",
    "risk": "有风险",
    "fail": "失败",
}


def _label(status):
    return STATUS_LABELS.get(status, status)


def _format_check_table(checks):
    lines = ["| 检查项 | 状态 | 说明 |", "| --- | --- | --- |"]
    for item in checks:
        lines.append(
            "| {name} | {status} | {message} |".format(
                name=item.get("name", ""),
                status=_label(item.get("status", "")),
                message=str(item.get("message", "")).replace("|", "\\|"),
            )
        )
    return "\n".join(lines)


def _details_for(checks, names):
    chunks = []
    for item in checks:
        if item.get("name") in names and item.get("details"):
            chunks.append(f"### {item['name']}\n\n```text\n{item['details']}\n```")
    return "\n\n".join(chunks) if chunks else "无。"


def _filter_checks(checks, prefixes=(), names=()):
    selected = []
    for item in checks:
        name = item.get("name", "")
        if name in names or any(name.startswith(prefix) for prefix in prefixes):
            selected.append(item)
    return selected


def render_report(health_result):
    config = health_result.get("config") or {}
    controllers = config.get("controllers", {})
    hybrid = config.get("hybrid", {})
    validation = config.get("validation", {})
    control_checks = health_result.get("control_checks", [])
    data_checks = health_result.get("data_checks", [])

    port_checks = _filter_checks(control_checks, prefixes=("port_", "forbidden_port_"))
    web_checks = _filter_checks(control_checks, prefixes=("web_",))
    log_checks = _filter_checks(control_checks, names=("recent_logs",))

    external_links = ", ".join(
        f"dpid={item.get('dpid')}, port={item.get('port')}"
        for item in hybrid.get("external_link_ports", [])
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    final_label = _label(health_result.get("status", "fail"))

    return "\n".join([
        "# DRL-OR-S 虚实通信验收报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 最终结论：{final_label}",
        "",
        "## 配置摘要",
        "",
        f"- 外部网卡：{config.get('external_interface', '-')}",
        f"- 虚实边界：{external_links or '-'}",
        f"- 虚拟网关：{hybrid.get('gateway_ip', '-')}",
        f"- 真实网段：{', '.join(hybrid.get('real_routes', [])) or '-'}",
        f"- 验证源主机：{validation.get('virtual_host_name', '-')} ({validation.get('virtual_host_ip', '-')})",
        f"- 真实主机：{validation.get('real_host_ip', '-')}",
        "",
        "## 服务端口状态",
        "",
        _format_check_table(port_checks),
        "",
        "## 控制器状态",
        "",
        f"- 期望控制器端口：{', '.join(str(p) for p in controllers.get('ports', [])) or '-'}",
        f"- 禁用控制器端口：{', '.join(str(p) for p in controllers.get('forbidden_ports', [])) or '-'}",
        "",
        "## Web API 状态",
        "",
        _format_check_table(web_checks),
        "",
        "## 虚实边界状态",
        "",
        f"- 静态边界端口：{external_links or '-'}",
        f"- 网关 MAC：{hybrid.get('gateway_mac', '-')}",
        f"- 检查说明：跨 VM 边界 LLDP 不作为验收前提，使用静态配置确认边界。",
        "",
        "## 数据面验证",
        "",
        _format_check_table(data_checks),
        "",
        "## 数据面输出摘要",
        "",
        _details_for(data_checks, {"host_route", "warmup_ping", "verification_ping", "ovs_flows"}),
        "",
        "## 最近严重日志",
        "",
        _format_check_table(log_checks),
        "",
        _details_for(log_checks, {"recent_logs"}),
        "",
    ])


def write_report(health_result, reports_dir="reports"):
    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = reports_path / f"acceptance-report-{timestamp}.md"
    path.write_text(render_report(health_result), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate hybrid acceptance Markdown report.")
    parser.add_argument("--config", default="config/hybrid_acceptance.json")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args(argv)

    health = run_health(args.config)
    path = write_report(health, args.reports_dir)
    print(path)
    return 0 if health.get("status") == "pass" else 2 if health.get("status") == "risk" else 1


if __name__ == "__main__":
    raise SystemExit(main())
