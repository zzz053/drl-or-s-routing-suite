# 验收 P0 功能设计

## 目标

本设计的目标是为当前项目补齐最低限度的验收运行能力，使一台 Linux 虚拟机被拷贝到验收单位的封闭系统后，可以连接真实 SDN 交换机网络，并通过固定命令完成启动、检查和报告生成。

P0 范围只覆盖验收必需能力：

- 一键启动、停止、健康检查、报告生成。
- VM 部署说明。
- 静态虚实边界配置。
- 自动生成 Markdown 验收报告。
- Web 首页展示虚实通信就绪状态。

本设计不尝试把项目改造成完整 SDN 产品，不替换现有 Ryu/Mininet 架构，也不改变 DRL 路径选择模型。

## 运行假设

- 验收环境是一台 Linux VM。
- 操作人员允许在 VM 内使用 `sudo`。
- 真实交换机网络可以通过 VM 可见网卡接入。
- 不能假设跨 VM 边界的 LLDP 一定可用。
- 因此虚实边界链路必须支持静态配置。
- 项目现有核心组件保持不变：
  - `server_agent.py`
  - `drl-or-s/path_service.py`
  - `start_controllers_test.py`
  - `testbed/creat_test_topo.py`
  - Flask Web UI，端口 `6009`

## 推荐方案

新增一个 Shell 验收入口脚本，并配合 Python 辅助工具：

```bash
./acceptance.sh start
./acceptance.sh stop
./acceptance.sh health
./acceptance.sh report
```

入口脚本使用 Bash，因为 Mininet、OVS、进程管理和 sudo 边界操作本身更适合 shell。配置解析、健康检查和报告生成由 Python 工具完成。

操作人员应以普通用户身份运行：

```bash
./acceptance.sh start
```

脚本只在 Mininet/OVS 操作处调用 `sudo`。`server_agent`、`path_service` 和 Ryu 控制器均以普通项目用户运行，避免出现 root 拥有的日志、pid 文件和 Python 环境问题。

## 配置文件

新增标准库可解析的 JSON 配置文件：

```text
config/hybrid_acceptance.json
```

初始结构如下：

```json
{
  "external_interface": "eno1",
  "controllers": {
    "ports": [6654, 6655, 6656, 6657, 6658, 6659, 6670],
    "forbidden_ports": [6671]
  },
  "hybrid": {
    "external_link_ports": [
      {"dpid": 1, "port": 20}
    ],
    "gateway_ip": "10.0.0.254",
    "gateway_mac": "02:00:00:00:fe:01",
    "real_routes": ["192.168.103.0/24"]
  },
  "validation": {
    "virtual_host_name": "h28",
    "virtual_host_ip": "10.0.0.28",
    "real_host_ip": "192.168.103.3",
    "expected_real_switch_dpid": 128986965761
  }
}
```

选择 JSON 而不是 YAML 的原因是：Python 标准库可以直接解析 JSON，不需要额外安装 PyYAML。验收 VM 处于封闭环境时，少一个依赖就少一个失败点。

该配置会映射为现有运行环境变量：

- `EXTERNAL_LINK_PORTS=1:20`
- `HYBRID_GATEWAY_IP=10.0.0.254`
- `HYBRID_GATEWAY_MAC=02:00:00:00:fe:01`
- `HYBRID_REAL_ROUTES=192.168.103.0/24`

## 验收入口脚本行为

### `./acceptance.sh start`

职责：

1. 读取 `config/hybrid_acceptance.json`。
2. 检查必需文件和工具是否存在。
3. 检查配置的外部网卡是否存在。
4. 根据配置导出运行环境变量。
5. 使用 `nohup setsid` 启动 `path_service`。
6. 使用 `nohup setsid` 启动 `server_agent.py hybrid`。
7. 通过 `start_controllers_test.py start -n` 启动 7 个 Ryu 控制器。
8. 使用配置的外部网卡后台启动 Mininet 拓扑。
9. 在 `logs/` 下写入 pid 文件。
10. 打印后续健康检查和报告生成命令。

Mininet 后台启动采用已验证过的方式：

```bash
tail -f /dev/null | sudo -E python3 -u testbed/creat_test_topo.py "$EXTERNAL_INTF"
```

这里使用 `sudo -E`，是为了让 `EXTERNAL_LINK_PORTS`、`HYBRID_GATEWAY_IP`、`HYBRID_REAL_ROUTES` 等环境变量在 sudo 后仍然传递给拓扑脚本。

### `./acceptance.sh stop`

职责：

1. 通过 `start_controllers_test.py stop` 停止 Ryu 控制器。
2. 根据 pid 文件停止后台进程。
3. 执行 `sudo mn -c` 清理 Mininet/OVS 残留。
4. 不修改源代码、配置、报告和 Git 提交。

`stop` 必须是幂等的。多次运行不应破坏验收环境。

### `./acceptance.sh health`

职责：

1. 运行 Python 健康检查工具。
2. 输出中文可读摘要。
3. 只有控制面关键要求失败时才返回失败码。

健康检查分为两级。

控制面检查：

- `server_agent` 端口 `6001` 正在监听。
- Web 端口 `6009` 正在监听。
- `path_service` 端口 `8889` 正在监听。
- Ryu 端口 `6654,6655,6656,6657,6658,6659,6670` 正在监听。
- 禁用端口 `6671` 未监听。
- `/api/health` 可访问。
- `/api/statistics` 可访问。
- `/api/acceptance/status` 可访问。
- 最近日志中没有新的严重错误，例如 `Traceback`、`AttributeError`、`root_disconnected`、`local variable`、Barrier handler 异常等。

数据面检查：

- 配置的 Mininet 主机 namespace 存在，例如 `h28`。
- 配置的真实网段路由存在，例如 `192.168.103.0/24 via 10.0.0.254`。
- 从 `h28` 到 `192.168.103.3` 执行 warmup ping。
- 从 `h28` 到 `192.168.103.3` 执行 verification ping。
- 检查 `s28` 和 `s1` 上是否存在 `10.0.0.28 <-> 192.168.103.3` 的 `idle_timeout=120` 双向流表。

如果因为 Mininet 未运行或 sudo 不可用导致数据面检查无法执行，只要控制面正常，整体状态应标记为 `有风险`，而不是直接标记为 `失败`。

### `./acceptance.sh report`

职责：

1. 执行与 `health` 相同的检查。
2. 在 `reports/` 下生成 Markdown 报告：

```text
reports/acceptance-report-YYYYMMDD-HHMMSS.md
```

3. 打印报告路径。

报告必须包含：

- 配置摘要。
- 服务端口状态。
- 控制器状态。
- Web API 状态。
- 虚实边界状态。
- h28 路由输出。
- warmup ping 结果。
- verification ping 结果。
- `s28/s1` 流表摘要。
- 最近严重日志。
- 最终结论：`通过`、`有风险` 或 `失败`。

## Python 辅助模块

### `tools/acceptance_config.py`

职责：

- 加载 JSON 配置。
- 校验必需配置段。
- 将 `external_link_ports` 转换为 `EXTERNAL_LINK_PORTS` 字符串。
- 将 `real_routes` 转换为 `HYBRID_REAL_ROUTES` 字符串。
- 只对安全项提供默认值：
  - `controllers.ports`
  - `controllers.forbidden_ports`
  - `hybrid.gateway_ip`
  - `hybrid.gateway_mac`

该模块不能静默猜测 `external_interface` 或 `real_host_ip`。

### `tools/acceptance_health.py`

职责：

- 读取配置。
- 执行本地控制面检查。
- 在可用时执行 Mininet/OVS 数据面检查。
- 支持结构化 JSON 输出。
- 默认输出简短中文摘要。

建议 CLI：

```bash
python3 tools/acceptance_health.py --config config/hybrid_acceptance.json
python3 tools/acceptance_health.py --config config/hybrid_acceptance.json --json
```

退出码：

- `0`：通过
- `1`：失败
- `2`：有风险

### `tools/generate_acceptance_report.py`

职责：

- 以程序方式调用健康检查逻辑。
- 渲染 Markdown 报告。
- 保存到 `reports/`。
- 返回报告路径。

建议 CLI：

```bash
python3 tools/generate_acceptance_report.py --config config/hybrid_acceptance.json
```

## Web 验收状态

新增 API：

```text
GET /api/acceptance/status
```

该接口不能执行 ping，也不能执行 sudo。它只报告控制面就绪情况和最近 route session 证据。

示例响应：

```json
{
  "status": "ready",
  "virtual_host_ip": "10.0.0.28",
  "real_host_ip": "192.168.103.3",
  "controllers_expected": 7,
  "controllers_connected": 7,
  "drl_connected": true,
  "hybrid_gateway_ip": "10.0.0.254",
  "real_routes": ["192.168.103.0/24"],
  "recent_route_session": {
    "src_ip": "10.0.0.28",
    "dst_ip": "192.168.103.3",
    "path_source": "dijkstra"
  },
  "issues": []
}
```

状态值：

- `ready`：控制面就绪，并且存在匹配的最近 route session。
- `partial`：控制面就绪，但还没有匹配的 route session。
- `not_ready`：控制器、DRL 服务或图状态不完整。
- `unknown`：配置无法加载。

Web 首页顶部新增一张验收状态卡片：

```text
虚实通信状态：ready / partial / not_ready / unknown
虚拟主机：10.0.0.28
真实主机：192.168.103.3
控制器：7/7
DRL服务：已连接/未连接
最近路径：10.0.0.28 -> 192.168.103.3
```

该卡片必须明确标注为“控制面/最近路径状态”，不能宣称这是实时 ping 结果。

## VM 部署文档

新增：

```text
docs/vm-acceptance-deployment.md
```

必须包含以下部分：

1. VM 拷贝和首次启动。
2. 必需 Linux 包和 Python 环境。
3. 网卡模式和接口命名。
4. 真实交换机接线方式。
5. 如何编辑 `config/hybrid_acceptance.json`。
6. 如何启动系统。
7. 如何运行健康检查。
8. 如何生成报告。
9. 常见故障处理：
   - 网卡名错误。
   - sudo 权限缺失。
   - `6671` 异常监听。
   - Ryu 端口缺失。
   - 没有 route session。
   - 第一次 ping 失败但第二次 ping 通过。
   - 跨 VM 边界 LLDP 不工作。

## 错误处理要求

验收工具必须输出可执行的错误信息：

- 配置文件缺失：打印准确路径和期望命令。
- JSON 格式错误：打印 `json.JSONDecodeError` 的行列信息。
- 外部网卡不存在：打印当前 `ip link` 中可见的网卡名。
- 端口缺失：列出期望监听端口和实际监听端口。
- 禁用端口正在监听：标记失败，并尽量显示对应进程。
- Web API 不可访问：显示 URL 和请求失败原因。
- 数据面检查不可用：标记有风险，并说明是 Mininet 未运行还是 sudo 不可用。
- ping 失败：包含 warmup 和 verification 两轮输出。
- 流表检查失败：尽量附带实际 `ovs-ofctl dump-flows` 摘要。

## 测试策略

实现时采用测试先行。

测试应覆盖：

- 配置解析和校验。
- `EXTERNAL_LINK_PORTS` 格式化。
- `HYBRID_REAL_ROUTES` 格式化。
- 健康状态分类：
  - 全部通过。
  - 控制面失败。
  - 数据面不可用。
  - 禁用端口正在监听。
- 报告渲染包含必需章节。
- `acceptance.sh` 包含必需命令分支，并使用配置驱动的环境变量。
- Web API 暴露 `/api/acceptance/status`。
- Web UI 包含验收状态卡片，并且不宣称实时 ping 状态。

涉及真实 Mininet/OVS/root 权限的集成验证需要手工执行。自动化测试应优先测试纯解析、分类和渲染逻辑，或使用命令输出样例进行测试。

## 范围边界

P0 包含：

- 本地 VM 验收流程。
- 静态虚实边界配置。
- 本地健康检查和报告工具。
- Web 控制面状态卡片。
- 部署文档。

P0 不包含：

- SSH 登录真实交换机。
- 自动配置真实交换机。
- YAML 支持。
- 用完整 Python CLI 替换 shell。
- DRL 模型行为改造。
- Web 拓扑大改版。
- 多租户或生产级部署加固。

## 成功标准

P0 完成标准：

1. 操作人员可以编辑 `config/hybrid_acceptance.json`。
2. `./acceptance.sh start` 可以启动验收环境。
3. `./acceptance.sh health` 可以输出 `通过`、`有风险` 或 `失败`，并提供可执行问题说明。
4. `./acceptance.sh report` 可以在 `reports/` 下生成 Markdown 报告。
5. Web UI 可以展示虚实验收状态卡片。
6. `./acceptance.sh stop` 可以清理运行环境。
7. 核心测试和语法检查通过。
8. 工具不依赖项目现有运行环境之外的额外 Python 包。
