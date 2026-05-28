# 验收 P0 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为项目实现验收必需的一键启动、停止、健康检查、报告生成、静态虚实边界配置、VM 部署说明和 Web 首页虚实通信状态展示。

**Architecture:** 保持现有 `server_agent.py`、Ryu 控制器、`path_service` 和 Mininet 拓扑结构不变，在外层新增验收入口脚本和 Python 标准库工具。Python 工具负责 JSON 配置解析、健康检查分类、Markdown 报告渲染和 Web 控制面状态计算；Shell 脚本只负责进程编排和 sudo 边界操作。

**Tech Stack:** Bash、Python 标准库、Flask 现有 API、pytest、Mininet/OVS/Ryu 现有运行环境。

---

## 文件结构

- Create: `config/hybrid_acceptance.json`
  - 保存验收环境静态配置，包括外部网卡、控制器端口、虚实边界、虚拟主机和真实主机。
- Create: `tools/acceptance_config.py`
  - 解析 JSON 配置，校验必填字段，生成 `EXTERNAL_LINK_PORTS`、`HYBRID_REAL_ROUTES` 等运行环境变量。
- Create: `tools/acceptance_health.py`
  - 执行控制面和数据面健康检查，支持中文摘要和 JSON 输出。
- Create: `tools/generate_acceptance_report.py`
  - 调用健康检查逻辑并生成 Markdown 验收报告。
- Create: `tools/acceptance_web_status.py`
  - 根据配置和 `server_agent` 当前内存状态生成 Web 验收状态，不执行 ping 或 sudo。
- Create: `acceptance.sh`
  - 提供 `start|stop|health|report` 统一入口。
- Create: `docs/vm-acceptance-deployment.md`
  - 中文 VM 部署和验收运行说明。
- Modify: `web_api.py`
  - 新增 `/api/acceptance/status`。
- Modify: `web_ui_html.py`
  - 首页顶部新增“虚实通信状态”卡片，并轮询 `/api/acceptance/status`。
- Test: `tests/test_acceptance_config.py`
- Test: `tests/test_acceptance_health.py`
- Test: `tests/test_acceptance_report.py`
- Test: `tests/test_acceptance_scripts.py`
- Test: `tests/test_acceptance_web_status.py`
- Test: `tests/test_web_acceptance_status.py`

### Task 1: 配置解析工具

**Files:**
- Create: `config/hybrid_acceptance.json`
- Create: `tools/acceptance_config.py`
- Test: `tests/test_acceptance_config.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
import json
import pytest

from tools.acceptance_config import (
    AcceptanceConfigError,
    build_runtime_env,
    format_external_link_ports,
    load_acceptance_config,
)


def write_config(tmp_path, data):
    path = tmp_path / "hybrid_acceptance.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def valid_config():
    return {
        "external_interface": "eno1",
        "controllers": {
            "ports": [6654, 6655, 6656, 6657, 6658, 6659, 6670],
            "forbidden_ports": [6671],
        },
        "hybrid": {
            "external_link_ports": [{"dpid": 1, "port": 20}],
            "gateway_ip": "10.0.0.254",
            "gateway_mac": "02:00:00:00:fe:01",
            "real_routes": ["192.168.103.0/24"],
        },
        "validation": {
            "virtual_host_name": "h28",
            "virtual_host_ip": "10.0.0.28",
            "real_host_ip": "192.168.103.3",
            "expected_real_switch_dpid": 128986965761,
        },
    }


def test_load_acceptance_config_validates_and_preserves_required_values(tmp_path):
    cfg = load_acceptance_config(write_config(tmp_path, valid_config()))

    assert cfg["external_interface"] == "eno1"
    assert cfg["controllers"]["ports"] == [6654, 6655, 6656, 6657, 6658, 6659, 6670]
    assert cfg["validation"]["real_host_ip"] == "192.168.103.3"


def test_load_acceptance_config_does_not_guess_external_interface(tmp_path):
    data = valid_config()
    data.pop("external_interface")

    with pytest.raises(AcceptanceConfigError, match="external_interface"):
        load_acceptance_config(write_config(tmp_path, data))


def test_format_external_link_ports_supports_multiple_pairs():
    cfg = valid_config()
    cfg["hybrid"]["external_link_ports"] = [
        {"dpid": 1, "port": 20},
        {"dpid": 42, "port": 7},
    ]

    assert format_external_link_ports(cfg) == "1:20,42:7"


def test_build_runtime_env_maps_config_to_existing_variables():
    env = build_runtime_env(valid_config())

    assert env["EXTERNAL_INTF"] == "eno1"
    assert env["EXTERNAL_LINK_PORTS"] == "1:20"
    assert env["HYBRID_GATEWAY_IP"] == "10.0.0.254"
    assert env["HYBRID_GATEWAY_MAC"] == "02:00:00:00:fe:01"
    assert env["HYBRID_REAL_ROUTES"] == "192.168.103.0/24"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_acceptance_config.py -q`

Expected: FAIL，错误原因是 `tools.acceptance_config` 尚不存在。

- [ ] **Step 3: 实现最小配置工具**

实现 `AcceptanceConfigError`、`load_acceptance_config(path)`、`format_external_link_ports(config)`、`format_real_routes(config)`、`build_runtime_env(config)`，并提供 CLI：

```bash
python3 tools/acceptance_config.py --config config/hybrid_acceptance.json --shell-env
```

CLI 输出格式必须可被 Bash `eval` 使用，例如：

```bash
export EXTERNAL_INTF='eno1'
export EXTERNAL_LINK_PORTS='1:20'
export HYBRID_GATEWAY_IP='10.0.0.254'
export HYBRID_GATEWAY_MAC='02:00:00:00:fe:01'
export HYBRID_REAL_ROUTES='192.168.103.0/24'
```

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_acceptance_config.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add config/hybrid_acceptance.json tools/acceptance_config.py tests/test_acceptance_config.py
git commit -m "Add hybrid acceptance config parser"
```

### Task 2: 健康检查工具

**Files:**
- Create: `tools/acceptance_health.py`
- Test: `tests/test_acceptance_health.py`

- [ ] **Step 1: 写失败测试**

```python
from tools.acceptance_health import (
    CheckResult,
    classify_health,
    command_contains_route,
    flow_output_has_bidirectional_flows,
)


def test_classify_health_passes_when_control_and_data_checks_pass():
    control = [CheckResult("server_agent", "pass", "6001 listening")]
    data = [CheckResult("ping", "pass", "verification ping passed")]

    assert classify_health(control, data) == "pass"


def test_classify_health_fails_when_control_check_fails():
    control = [CheckResult("ryu_6654", "fail", "port missing")]
    data = [CheckResult("ping", "pass", "verification ping passed")]

    assert classify_health(control, data) == "fail"


def test_classify_health_returns_risk_when_data_plane_unavailable():
    control = [CheckResult("server_agent", "pass", "6001 listening")]
    data = [CheckResult("mininet_h28", "risk", "host namespace unavailable")]

    assert classify_health(control, data) == "risk"


def test_command_contains_route_matches_real_subnet_gateway():
    output = "192.168.103.0/24 via 10.0.0.254 dev h28-eth0\n"

    assert command_contains_route(output, "192.168.103.0/24", "10.0.0.254")


def test_flow_output_has_bidirectional_idle_timeout_flows():
    s28 = "idle_timeout=120,ip,nw_src=10.0.0.28,nw_dst=192.168.103.3 actions=output:1\n"
    s1 = "idle_timeout=120,ip,nw_src=192.168.103.3,nw_dst=10.0.0.28 actions=output:2\n"

    assert flow_output_has_bidirectional_flows(
        [s28, s1],
        virtual_ip="10.0.0.28",
        real_ip="192.168.103.3",
    )
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_acceptance_health.py -q`

Expected: FAIL，错误原因是 `tools.acceptance_health` 尚不存在。

- [ ] **Step 3: 实现健康检查核心**

实现：

- `CheckResult(name, status, message, details="")`
- `classify_health(control_checks, data_checks)`
- `command_contains_route(output, cidr, gateway_ip)`
- `flow_output_has_bidirectional_flows(outputs, virtual_ip, real_ip)`
- `run_health(config_path, json_output=False)`

运行时检查包括：

- TCP 端口：`6001`、`6009`、`8889`、Ryu 配置端口。
- 禁用端口：`6671` 不应监听。
- Web API：`/api/health`、`/api/statistics`、`/api/acceptance/status`。
- 严重日志关键词：`Traceback`、`AttributeError`、`root_disconnected`、`local variable`、`barrier`。
- 数据面：通过 `pgrep -f mininet:h28` 查找 host 进程，再用 `sudo mnexec -a <pid>` 执行 `ip route` 和两轮 ping；无法执行时标记 `risk`。
- OVS：检查 `s28`、`s1` 的 `ovs-ofctl dump-flows` 输出是否含双向 `idle_timeout=120` 流。

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_acceptance_health.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tools/acceptance_health.py tests/test_acceptance_health.py
git commit -m "Add hybrid acceptance health checks"
```

### Task 3: 报告生成工具

**Files:**
- Create: `tools/generate_acceptance_report.py`
- Test: `tests/test_acceptance_report.py`

- [ ] **Step 1: 写失败测试**

```python
from tools.acceptance_health import CheckResult
from tools.generate_acceptance_report import render_report


def test_render_report_includes_required_acceptance_sections():
    health = {
        "status": "risk",
        "config": {
            "external_interface": "eno1",
            "controllers": {"ports": [6654], "forbidden_ports": [6671]},
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
    }

    report = render_report(health)

    assert "# DRL-OR-S 虚实通信验收报告" in report
    assert "## 配置摘要" in report
    assert "## 服务端口状态" in report
    assert "## 控制器状态" in report
    assert "## Web API 状态" in report
    assert "## 虚实边界状态" in report
    assert "## 数据面验证" in report
    assert "## 最近严重日志" in report
    assert "最终结论：有风险" in report
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_acceptance_report.py -q`

Expected: FAIL，错误原因是 `tools.generate_acceptance_report` 尚不存在。

- [ ] **Step 3: 实现报告渲染和保存**

实现 `render_report(health_result)` 和 CLI：

```bash
python3 tools/generate_acceptance_report.py --config config/hybrid_acceptance.json
```

输出路径格式：

```text
reports/acceptance-report-YYYYMMDD-HHMMSS.md
```

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_acceptance_report.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tools/generate_acceptance_report.py tests/test_acceptance_report.py
git commit -m "Add hybrid acceptance report generator"
```

### Task 4: 验收入口脚本和 VM 文档

**Files:**
- Create: `acceptance.sh`
- Create: `docs/vm-acceptance-deployment.md`
- Test: `tests/test_acceptance_scripts.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_script_exposes_required_commands():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert 'case "$COMMAND" in' in text
    for command in ["start)", "stop)", "health)", "report)"]:
        assert command in text


def test_acceptance_script_uses_config_driven_environment_and_sudo_boundary():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert "tools/acceptance_config.py" in text
    assert "--shell-env" in text
    assert "sudo -E" in text
    assert "testbed/creat_test_topo.py" in text
    assert "start_controllers_test.py start -n" in text
    assert "start_controllers_test.py stop" in text
    assert "sudo mn -c" in text


def test_vm_acceptance_doc_mentions_static_boundary_and_lldp_limitation():
    text = (ROOT / "docs" / "vm-acceptance-deployment.md").read_text(encoding="utf-8")

    assert "config/hybrid_acceptance.json" in text
    assert "LLDP" in text
    assert "静态虚实边界" in text
    assert "./acceptance.sh start" in text
    assert "./acceptance.sh health" in text
    assert "./acceptance.sh report" in text
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_acceptance_scripts.py -q`

Expected: FAIL，错误原因是 `acceptance.sh` 或 VM 文档尚不存在。

- [ ] **Step 3: 实现脚本和文档**

`acceptance.sh start` 必须：

- 通过 `tools/acceptance_config.py --shell-env` 加载配置。
- 启动 `path_service`、`server_agent.py hybrid`、`start_controllers_test.py start -n`。
- 后台启动 Mininet：

```bash
tail -f /dev/null | sudo -E python3 -u testbed/creat_test_topo.py "$EXTERNAL_INTF"
```

`acceptance.sh stop` 必须：

- 执行 `python3 start_controllers_test.py stop || true`
- 停止 `logs/*.pid`
- 执行 `sudo mn -c || true`

文档必须覆盖 VM 拷贝、网卡模式、真实交换机接线、静态配置、启动、健康检查、报告生成和常见故障。

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_acceptance_scripts.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add acceptance.sh docs/vm-acceptance-deployment.md tests/test_acceptance_scripts.py
git commit -m "Add acceptance entrypoint and VM deployment guide"
```

### Task 5: Web 验收状态 API

**Files:**
- Create: `tools/acceptance_web_status.py`
- Modify: `web_api.py`
- Test: `tests/test_acceptance_web_status.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path

from tools.acceptance_web_status import build_acceptance_status


class FakeAgent:
    def __init__(self):
        self.clients = {("127.0.0.1", 6654): object()}
        self.path_service_sock = object()
        self.G = type("Graph", (), {"nodes": lambda self: [1], "edges": lambda self: [(1, 2)]})()
        self.controller_route_sessions = {
            ("127.0.0.1", 6654): [
                {
                    "src_ip": "10.0.0.28",
                    "dst_ip": "192.168.103.3",
                    "path_source": "dijkstra",
                    "updated_at": 100,
                }
            ]
        }


def test_build_acceptance_status_ready_when_control_plane_and_recent_session_match(tmp_path):
    config_path = tmp_path / "hybrid_acceptance.json"
    config_path.write_text(
        '{"external_interface":"eno1","controllers":{"ports":[6654],"forbidden_ports":[6671]},'
        '"hybrid":{"external_link_ports":[{"dpid":1,"port":20}],"gateway_ip":"10.0.0.254",'
        '"gateway_mac":"02:00:00:00:fe:01","real_routes":["192.168.103.0/24"]},'
        '"validation":{"virtual_host_name":"h28","virtual_host_ip":"10.0.0.28",'
        '"real_host_ip":"192.168.103.3"}}',
        encoding="utf-8",
    )

    status = build_acceptance_status(FakeAgent(), config_path=config_path)

    assert status["status"] == "ready"
    assert status["controllers_expected"] == 1
    assert status["controllers_connected"] == 1
    assert status["drl_connected"] is True
    assert status["recent_route_session"]["src_ip"] == "10.0.0.28"


def test_web_api_registers_acceptance_status_route():
    text = Path("web_api.py").read_text(encoding="utf-8")

    assert "/api/acceptance/status" in text
    assert "build_acceptance_status" in text
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_acceptance_web_status.py -q`

Expected: FAIL，错误原因是 `tools.acceptance_web_status` 尚不存在，或 API 路由尚未注册。

- [ ] **Step 3: 实现 API 状态计算和路由**

实现 `build_acceptance_status(server_agent, config_path="config/hybrid_acceptance.json")`：

- 配置加载失败返回 `status: "unknown"`。
- 控制器数量不足、DRL 未连接或图为空返回 `not_ready`。
- 控制面就绪但没有匹配 `10.0.0.28 -> 192.168.103.3` 的 route session 返回 `partial`。
- 控制面就绪且有匹配 route session 返回 `ready`。
- 不执行 ping、不调用 sudo。

在 `web_api.py` 注册：

```python
@app.route('/api/acceptance/status', methods=['GET'])
def get_acceptance_status():
    server_agent = get_server_agent()
    if server_agent is None:
        return jsonify({'status': 'unknown', 'issues': ['Server not initialized']}), 503
    return jsonify(build_acceptance_status(server_agent))
```

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_acceptance_web_status.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tools/acceptance_web_status.py web_api.py tests/test_acceptance_web_status.py
git commit -m "Add Web acceptance status API"
```

### Task 6: Web 首页状态卡片

**Files:**
- Modify: `web_ui_html.py`
- Test: `tests/test_web_acceptance_status.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_homepage_contains_acceptance_status_card():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "acceptance-status-card" in text
    assert "虚实通信状态" in text
    assert "控制面/最近路径状态" in text
    assert "/api/acceptance/status" in text
    assert "实时 ping" not in text
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_web_acceptance_status.py -q`

Expected: FAIL，错误原因是页面还没有验收状态卡片。

- [ ] **Step 3: 实现页面卡片**

在页面顶部加入状态卡片，显示：

- `虚实通信状态`
- `控制面/最近路径状态`
- 虚拟主机 IP
- 真实主机 IP
- 控制器连接数量
- DRL 服务连接状态
- 最近路径

新增 JS 函数 `updateAcceptanceStatus()`，通过 `fetch('/api/acceptance/status')` 更新卡片，并纳入现有定时刷新流程。

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_web_acceptance_status.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add web_ui_html.py tests/test_web_acceptance_status.py
git commit -m "Show hybrid acceptance status on Web homepage"
```

### Task 7: 总体验证

**Files:**
- No new files.

- [ ] **Step 1: 运行验收相关测试**

Run:

```bash
pytest tests/test_acceptance_config.py tests/test_acceptance_health.py tests/test_acceptance_report.py tests/test_acceptance_scripts.py tests/test_acceptance_web_status.py tests/test_web_acceptance_status.py -q
```

Expected: PASS。

- [ ] **Step 2: 运行现有核心测试**

Run:

```bash
pytest tests/test_delivery_scripts.py tests/test_hybrid_gateway.py tests/test_hybrid_external_interface.py tests/test_external_link_ports_config.py tests/test_server_agent_cli.py -q
```

Expected: PASS。

- [ ] **Step 3: 语法检查**

Run:

```bash
python -m py_compile tools/acceptance_config.py tools/acceptance_health.py tools/generate_acceptance_report.py tools/acceptance_web_status.py web_api.py web_ui_html.py server_agent.py
```

Expected: exit code 0。

- [ ] **Step 4: 脚本静态检查**

Run:

```bash
bash -n acceptance.sh
```

Expected: exit code 0。

- [ ] **Step 5: 提交最终验证修正**

如果验证暴露小问题，只提交与 P0 验收功能直接相关的修正：

```bash
git add acceptance.sh config/hybrid_acceptance.json docs/vm-acceptance-deployment.md tools/acceptance_config.py tools/acceptance_health.py tools/generate_acceptance_report.py tools/acceptance_web_status.py web_api.py web_ui_html.py tests/test_acceptance_config.py tests/test_acceptance_health.py tests/test_acceptance_report.py tests/test_acceptance_scripts.py tests/test_acceptance_web_status.py tests/test_web_acceptance_status.py
git commit -m "Finalize acceptance P0 verification"
```

## 自检

- 设计稿中的一键启动、停止、健康检查和报告生成由 Task 2、Task 3、Task 4 覆盖。
- VM 部署说明由 Task 4 覆盖。
- 静态虚实边界配置由 Task 1、Task 4 覆盖。
- 自动生成验收报告由 Task 3 覆盖。
- Web 首页显示“虚实通信是否正常”由 Task 5、Task 6 覆盖。
- LLDP 不可靠场景通过静态配置和 VM 文档覆盖。
- 自动化测试不依赖真实 Mininet/OVS/root 权限；真实数据面验证保留在运行时健康检查中。
