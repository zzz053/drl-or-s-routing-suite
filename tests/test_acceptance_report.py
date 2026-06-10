from tools.acceptance_health import CheckResult
from tools.generate_acceptance_report import render_report


def test_render_report_includes_required_acceptance_sections():
    health = {
        "status": "risk",
        "config": {
            "external_interface": "eno1",
            "controllers": {"ports": [6654]},
            "hybrid": {
                "external_link_ports": [{"dpid": 1, "port": 20}],
                "gateway_ip": "10.0.0.254",
                "real_routes": ["192.168.103.0/24"],
            },
            "validation": {
                "virtual_host_name": "h28",
                "virtual_host_ip": "10.0.0.28",
                "real_host_ip": "192.168.103.3",
            },
        },
        "control_checks": [CheckResult("server_agent", "pass", "6001 listening").to_dict()],
        "data_checks": [CheckResult("verification_ping", "risk", "sudo unavailable").to_dict()],
        "feature_audit": {
            "status": "pass",
            "features": [
                {
                    "name": "web_route_session_highlight",
                    "status": "pass",
                    "message": "Web 路径会话点击高亮能力存在",
                }
            ],
        },
    }

    report = render_report(health)

    assert "# DRL-OR-S 虚实通信验收报告" in report
    assert "## 配置摘要" in report
    assert "## 服务端口状态" in report
    assert "## 控制器状态" in report
    assert "## Web API 状态" in report
    assert "## 虚实边界状态" in report
    assert "## 项目功能覆盖" in report
    assert "web_route_session_highlight" in report
    assert "## 数据面验证" in report
    assert "## 最近严重日志" in report
    assert "最终结论：有风险" in report
