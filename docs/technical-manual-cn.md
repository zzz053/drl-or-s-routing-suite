# DRL-OR-S Routing Suite 技术手册

本文档用于指导 DRL-OR-S Routing Suite 在 Linux/VM/真实 SDN 交换机环境中的部署、运行、验收测试和故障定位。文档采用通用参数描述，现场部署时应将 `<...>` 占位符替换为实际环境值。

## 1. 系统定位

DRL-OR-S Routing Suite 是一个多域 SDN 路由、仿真验证与虚实通信系统，主要能力包括：

- 启动多域 Ryu 控制器并管理 OpenFlow 交换机。
- 启动 `server_agent.py` 汇聚控制器状态、路径会话和 Web/API。
- 启动 `path_service.py` 提供 DRL、候选路径或 Dijkstra 路径计算链路。
- 启动项目内置 Mininet 拓扑进行仿真。
- 将 VM 内 Mininet 边界交换机端口接入真实 SDN 交换机，实现虚拟主机到真实主机通信。
- 在 Web 页面展示拓扑、路径会话、路径高亮、流表和验收状态。
- 自动执行健康检查并生成验收报告。

## 2. 架构概览

```text
浏览器 / API 客户端
        |
        v
server_agent.py  ----  path_service.py
        |
        v
Ryu controllers: <OPENFLOW_PORT_LIST>
        |
        v
Mininet 多域拓扑
        |
        v
边界 OVS 端口 <EXTERNAL_SWITCH>:<EXTERNAL_PORT>
        |
        v
VM 外部网卡 <EXTERNAL_INTERFACE>
        |
        v
真实 SDN 交换机 OpenFlow 端口
        |
        v
真实主机 <REAL_HOST_IP>
```

默认约定：

- Web UI：`6009`
- server socket：`6001`
- path service：`8889`
- 主控制器 OpenFlow 端口：`6654`
- 虚实边界默认使用静态配置，不依赖 VM 边界 LLDP 自动发现。

## 3. 功能说明

本节列出项目代码中已经具备的功能。部分能力依赖现场网络、真实交换机、DRL 模型或指定测试场景，手册会明确说明其工程用途；是否纳入正式验收，应以对应测试流程的结果为准。

### 3.1 控制器接入与拓扑管理

系统通过多实例 Ryu 控制器接入 Mininet OVS 和真实 OpenFlow 交换机。控制器负责：

- 维护交换机、端口、主机、链路、控制器归属和域间连接信息。
- 处理 LLDP、ARP、IPv4、流表统计、端口统计和 flow removed 等控制面事件。
- 向 `server_agent.py` 上报拓扑、流表、路径会话和控制器状态。
- 根据路径计算结果下发正向和反向 OpenFlow 1.3 流表。
- 使用 OpenFlow barrier 等待关键流表安装完成，降低首包先于流表转发的风险。

### 3.2 根控汇聚与多域协同

`server_agent.py` 是根控汇聚进程，负责维护全局视图：

- 维护控制器连接、控制器到交换机的映射、全局拓扑图和 Web 状态缓存。
- 汇聚各子控制器上报的主机、链路、端口、流表和路径会话。
- 接收跨域路径请求，并把最终路径返回给发起请求的控制器。
- 在控制器断连或心跳超时时标记相关链路风险，并清理受影响的路径会话。
- 向 Web/API 提供一致的只读视图和有限的调试控制接口。

### 3.3 路径计算模式

`server_agent.py` 接收控制器上报的路径请求，并按运行模式选择路径计算策略：

- `spf`：使用本地最短路径。
- `shadow`：调用 DRL 路径服务但实际安装 fallback 路径，用于观察 DRL 建议和稳定路径的差异。
- `hybrid`：优先采用通过校验的 DRL 路径，失败时回退 Dijkstra。
- `drl`：强制使用 DRL/path service 返回的有效路径，主要用于实验验证。

生产和验收环境推荐使用 `hybrid`。该模式的工程目标是“路径服务可用时利用 DRL，不可用时不中断业务”。

### 3.4 候选路径与路由策略

路径服务链路支持生成候选路径和策略化权重：

- `build_k_shortest_candidates` 可基于当前全局图生成 K 条候选路径。
- 候选路径包含交换机路径、跳数、时延、丢包率、最小带宽和利用率摘要。
- 支持按策略计算边权重：`shortest_path`、`min_delay`、`max_bandwidth`、`min_loss`、`hybrid`。
- 业务类型可通过 TCP/UDP 端口区间映射到 `task_type`，再映射到路由策略和流表优先级。
- DRL 服务不可用、返回无效路径或路径校验失败时，系统可回退到本地 Dijkstra/fallback 路径。

说明：当前项目已经接入 K 候选路径与 DRL 调用接口；具体 DRL 模型是否真正按候选路径逐条打分选择，需要结合所加载模型和 `path_service.py` 运行结果单独验证。

#### 端口业务分类与 DRL 对齐

项目通过 `traffic_classes` 把甲方要求的“按终端端口号区分业务”转换为 DRL 可理解的请求类型。运行链路为：

```text
TCP/UDP 源/目的端口 -> task_type -> route_policy / flow_priority -> drl_type / drl_demand_kbps / drl_duration
```

默认三类业务如下：

| 端口范围 | 业务类 | DRL rtype | 路由策略 | 流表优先级 | DRL 需求 |
| --- | --- | ---: | --- | ---: | ---: |
| `1-5000` | `task_0` | `0` | `min_delay` | `30` | `100Kbps` |
| `5001-10000` | `task_1` | `1` | `max_bandwidth` | `20` | `1500Kbps` |
| `10001-65535` | `task_2` | `2` | `hybrid` | `10` | `1500Kbps` |

分类规则是先匹配目的端口，再匹配源端口；非 TCP/UDP 流量或未命中端口范围时使用 `default`。`rtype 3` 是 DRL-OR-S 训练环境中的丢包敏感类型，当前甲方需求为三类业务，因此默认不对外暴露。

控制器把业务元数据放入路径请求和路径会话；`server_agent.py` 再把 `drl_type`、`drl_demand_kbps`、`drl_duration` 透传给 `drl-or-s/path_service.py`。如果 DRL 依赖或模型不可用，路径仍可回退到 Dijkstra/fallback，但 Web 和日志中仍会保留业务分类元数据。

### 3.5 流表下发与生命周期

控制器会根据路径结果安装端到端转发流表：

- 支持正向和反向路径流表。
- 支持 IPv4、ARP 以及带 TCP/UDP 端口条件的 L4 match。
- 自动路径流表使用可配置的 idle timeout 和 hard timeout。
- flow removed 事件会触发本地流表缓存清理，并同步影响路径会话状态。
- Web 手动流表通过 `manual_flow_mod` 下发，适合临时调试，不应替代正式路径计算。

### 3.6 链路故障与路径重规划

项目包含链路状态变化后的路径维护逻辑：

- 控制器检测链路删除或端口状态变化后，会失效受影响的路径会话。
- 对受影响会话尝试重新计算路径并重新安装流表。
- `server_agent.py` 在路径计算时会排除已知 down 链路。
- 当 stale link-down 状态阻断唯一可用路径时，系统保留忽略陈旧状态的 fallback 处理逻辑，并记录原因。
- Web 路径会话面板会在会话 ID 变化时尝试按会话签名重新关联当前选中路径，用于观察路径重规划后的变化。

该能力需要通过链路断开、端口关闭或拓扑变化场景验证；未执行该类测试时，只能说明代码具备对应处理逻辑。

### 3.7 主机学习与远端主机隔离

控制器和根控共同维护主机信息：

- 本地域主机通过 ARP/IPv4 packet-in 学习。
- 远端主机信息由 `server_agent.py` 跨控制器同步。
- 远端主机不会污染本地 host 表，避免跨域流量被误判为本地域内路径。
- 外部真实网络主机通过虚实边界逻辑处理，避免被错误写入虚拟交换机本地主机表。
- 可配置 `VIRTUAL_SWITCH_DPID_MAX`、`EXTERNAL_LINK_PORTS` 和真实网段，用于区分虚拟交换机、真实交换机和外部接入端口。

### 3.8 虚实通信

虚实通信功能将 VM 内的 Mininet 边界交换机端口接入真实 SDN 交换机。系统通过以下机制保证可迁移性：

- 使用 `config/hybrid_acceptance.json` 显式声明外部网卡、真实网段、虚拟网关和边界端口。
- 使用静态边界端口 `<EXTERNAL_SWITCH>:<EXTERNAL_PORT>`，避免依赖 VM 环境下不稳定的边界 LLDP。
- 由控制器响应虚拟网关 ARP，使 Mininet 主机可以把真实网段流量交给控制器路径逻辑。
- 由路径计算和流表下发逻辑建立虚拟网络到真实网络的双向转发。
- 通过外部网卡接入 OVS，把真实交换机端口纳入虚拟拓扑边界。
- 支持对真实网段配置显式路由，使 Mininet 主机访问真实主机时走虚拟网关。

### 3.9 链路指标与可观测数据

系统会采集并展示多类运行数据：

- LLDP/echo 相关的链路时延估计。
- 端口统计、带宽、空闲带宽、利用率和丢包相关字段。
- 交换机流表、命中包数、优先级、match 和 action。
- 路径会话中的路径来源、DRL 决策来源、fallback 原因、模型信息、置信度和计算耗时等元数据。
- Web 统计区展示控制器、交换机、主机、链路、路径服务连接状态和简化指标。

说明：Web 首页中的吞吐、时延等统计用于运行态观察，不能替代 `ping`、`iperf`、`tcpdump`、交换机计数器等验收级数据面证据。

### 3.10 Web/API 与调试能力

Web/API 提供运行态观测和调试入口：

- 控制器连接状态。
- 拓扑图和域间链路。
- 交换机流表查看。
- 路径会话查看。
- 路径会话点击高亮。
- 拓扑整理和精简布局。
- 手动流表下发和删除。
- 验收状态卡片。
- 健康检查和报告生成所需的状态接口。

### 3.11 自动化验收

`acceptance.sh` 封装了验收环境的生命周期：

- `start`：启动路径服务、汇聚服务、Ryu 控制器和 Mininet 拓扑。
- `stop`：停止服务并清理 Mininet/OVS 残留状态。
- `health`：检查控制面、Web API、Mininet 路由、数据面 ping 和关键流表。
- `report`：生成 Markdown 验收报告。

配套工具包括：

- `tools/acceptance_config.py`：校验验收配置并生成 shell 环境变量。
- `tools/acceptance_health.py`：执行控制面和数据面健康检查。
- `tools/generate_acceptance_report.py`：生成通用 Markdown 验收报告。
- `tools/acceptance_web_status.py`：构造 Web 首页验收状态。
- `tools/web_consistency_audit.py`：审计 Web 路径会话和拓扑/流表一致性。

### 3.12 启停脚本与交付能力

项目提供面向部署和调试的脚本：

- `start_suite.sh`：按顺序启动路径服务、根控、控制器和 Mininet。
- `stop_suite.sh`：停止服务并清理 Mininet/OVS 残留。
- `acceptance.sh`：面向验收的一键生命周期入口。
- `acceptance.sh audit`：静态审计项目核心功能是否仍被代码覆盖，适合在未接入真实交换机前先检查交付完整性。
- `acceptance.sh load`：在已运行的 Mininet 拓扑中随机选择主机对并发执行 `iperf3`，用于网络负载能力测试。
- `start_controllers_test.py`：批量启动/停止控制器。
- `testbed/creat_test_topo.py`：创建项目内置 Mininet 拓扑。
- `testbed/hybrid_external_interface.py`：把 VM 外部网卡接入边界 OVS。

## 4. Web 界面使用方法

访问地址：

```text
http://<SERVER_AGENT_IP>:6009
```

### 4.1 首页状态区

首页展示系统概览，包括控制器数量、拓扑节点/链路、DRL 连接状态和虚实通信状态。验收状态卡片通常有以下含义：

| 状态 | 含义 |
| --- | --- |
| `ready` | 控制面可用，并存在最近的虚实路径会话 |
| `partial` | 服务可用，但路径会话或部分证据不足 |
| `error` / `unknown` | 配置、服务或接口异常，需要查看 `issues` 或日志 |

### 4.2 拓扑图

拓扑图用于查看控制器、交换机、主机和链路关系。常用操作：

- 查看节点：点击控制器、交换机或主机节点，右侧面板显示详细信息。
- 查看链路：点击链路，右侧面板显示链路类型、端点和属性。
- 查看路径：当存在路径会话时，页面会展示路径相关信息，辅助确认跨域转发经过的交换机。
- 调整视图：可通过拖拽、缩放查看不同域和链路。
- 整理拓扑：点击“整理拓扑”会清除拖拽坐标，重新应用精简布局并保持当前路径高亮。
- 精简布局：Web 默认使用精简拓扑视图，按域组织交换机、主机和控制器，减少大拓扑下的视觉拥挤。
- 手动位置：交换机拖拽位置会保存在浏览器 localStorage 中；重新整理拓扑会清除这些位置。

### 4.3 节点详情面板

点击不同类型节点时，右侧面板展示不同信息：

- Root Controller：展示根控节点信息。
- Sub Controller：展示控制器 ID、连接状态和管理对象。
- Switch：展示 DPID、控制器归属、端口/流表入口和可执行操作。
- Host：展示主机 IP、MAC、接入交换机和接入口。

### 4.4 链路详情面板

点击链路后，右侧面板展示：

- 链路端点和端口。
- 链路类型，例如交换机链路、主机接入链路、控制器关系或外部链路。
- 时延、带宽、丢包率、域间关系等字段。
- down 链路或异常链路会使用不同颜色显示。

### 4.5 路径会话与路径高亮

路径会话记录系统最近处理过的源/目的地址、交换机路径和路径来源。Web 支持：

- 在路径会话列表中查看源/目的 IP、业务类型、路由策略、路径来源和 fallback 原因。
- 对按端口分类的业务，会显示 `task_type / rtype / demand / duration`，用于核对控制器分类和 DRL 请求语义是否一致。
- 点击某条路径会话后，高亮该会话经过的节点和链路。
- 高亮样式使用青色节点和虚线链路，便于和普通拓扑链路区分。
- 当路径重规划导致会话 ID 变化时，前端会尝试按源/目的、路径和策略签名重新匹配当前选中会话。
- 如果选中会话消失，页面会取消高亮，避免展示过期路径。

该功能用于观察路径选择和重规划结果；正式判断仍应结合流表、抓包和 ping/iperf 等数据面证据。

### 4.6 交换机流表

点击交换机节点后，右侧面板会加载该交换机的流表信息。重点关注：

- `priority`：流表优先级。
- `match`：匹配字段，例如入端口、源/目的 IP、协议。
- `action`：输出端口或 MAC 改写等动作。
- `packets`：命中包数，用于判断流表是否实际生效。

排查虚实通信时，应重点检查边界交换机和验证源主机所在交换机是否存在双向流表。

Web 为降低大拓扑刷新开销，默认 `/api/graph?include_flows=0` 不携带全量流表；只有选中交换机时才调用 `/api/switch/<SWITCH_ID>/flows` 懒加载流表。选中交换机后，页面会定期刷新该交换机流表。

### 4.7 手动流表操作

Web 支持面向调试的手动流表下发和删除：

1. 点击目标交换机节点。
2. 在右侧面板选择“手动下发流表”。
3. 填写输出端口、优先级、超时时间和匹配字段。
4. 检查 Match JSON 预览。
5. 提交后刷新该交换机流表确认结果。

注意事项：

- 手动流表适合临时验证，不建议作为正式验收路径依赖。
- 下发前应确认 `switch_id` 和 `out_port` 对应真实拓扑。
- 删除流表时应确认目标 `flow_id`，避免影响自动路径流表。
- 表单会生成 Match JSON 预览；复杂 match 建议先通过 API 或小规模场景验证。

### 4.8 验收状态

验收状态主要依据：

- 控制器连接数量。
- DRL/path service 是否连接。
- 验收源主机到真实主机是否存在最近路径会话。
- 配置中的虚拟主机、真实主机和真实网段是否一致。

如果页面状态不是 `ready`，优先查看 `/api/acceptance/status` 的 `issues` 字段，再结合日志定位。

### 4.9 Web 刷新与性能设计

Web 前端包含以下性能设计：

- 拓扑和验收状态默认按周期刷新。
- 图数据使用版本号和拓扑签名，避免无变化时重复重绘。
- 节点/链路采用增量同步，尽量保留当前视角和选择状态。
- 指标类变化不会强制重新布局，减少页面抖动。
- 边 ID 保持稳定，便于路径高亮和 hover 状态维护。
- 鼠标悬停链路只更新临时状态，不修改主图数据集。

这些优化主要面向演示和运维观察，不改变控制器的路径计算和流表下发行为。

### 4.10 常用 API

```bash
curl -s http://<SERVER_AGENT_IP>:6009/api/health
curl -s http://<SERVER_AGENT_IP>:6009/api/statistics
curl -s http://<SERVER_AGENT_IP>:6009/api/controllers
curl -s http://<SERVER_AGENT_IP>:6009/api/graph
curl -s 'http://<SERVER_AGENT_IP>:6009/api/graph?include_flows=1'
curl -s http://<SERVER_AGENT_IP>:6009/api/route_sessions
curl -s http://<SERVER_AGENT_IP>:6009/api/acceptance/status
curl -s http://<SERVER_AGENT_IP>:6009/api/switch/<SWITCH_ID>/flows
curl -s -X POST http://<SERVER_AGENT_IP>:6009/api/path \
  -H 'Content-Type: application/json' \
  -d '{"src":"<SRC_IP>","dst":"<DST_IP>"}'
```

API 用途：

| API | 用途 |
| --- | --- |
| `/api/health` | 基础服务存活检查 |
| `/api/statistics` | 控制器、交换机、主机、链路数量统计 |
| `/api/controllers` | 控制器连接和控制器-交换机关系 |
| `/api/topo` | 兼容旧前端或脚本的简化拓扑数据 |
| `/api/graph` | Web 拓扑图数据，默认不包含全量流表 |
| `/api/graph?include_flows=1` | 包含流表字段的拓扑图数据，适合脚本审计 |
| `/api/route_sessions` | 路径会话 |
| `/api/acceptance/status` | Web 首页验收状态 |
| `/api/switch/<SWITCH_ID>/flows` | 指定交换机流表 |
| `POST /api/path` | 手工请求一次路径计算 |
| `POST /api/flows` | 下发手动流表 |
| `DELETE /api/flows` | 删除手动流表 |

## 5. 目录结构

```text
drl-or-s-routing-suite/
  acceptance.sh                       # 一键启动/停止/健康检查/报告
  start_suite.sh                      # 交互式启动并进入 Mininet CLI
  stop_suite.sh                       # 停止交互式套件
  server_agent.py                     # 汇聚服务、Web/API、路径会话
  controller.py                       # Ryu 控制器
  common_config.py                    # 控制器公共配置
  config/hybrid_acceptance.json       # 验收配置
  tools/acceptance_config.py          # 配置校验
  tools/acceptance_health.py          # 健康检查
  tools/generate_acceptance_report.py # 报告生成
  testbed/creat_test_topo.py          # Mininet 拓扑
  testbed/hybrid_external_interface.py # 外部网卡接入 OVS
  drl-or-s/path_service.py            # 路径服务
  logs/                               # 日志
  reports/                            # 验收报告
```

## 6. 部署前置条件

推荐运行环境：

- Ubuntu 或兼容 Linux VM。
- Python 3。
- Mininet。
- Open vSwitch。
- Ryu。
- 能访问真实 SDN 交换机控制面和数据面。

安装系统依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv mininet openvswitch-switch tcpdump curl net-tools
```

安装 Python 依赖：

```bash
cd <PROJECT_DIR>
pip3 install -r requirements.txt
```

可选虚拟环境：

```bash
python3 -m venv <VENV_DIR>
source <VENV_DIR>/bin/activate
pip install -r requirements.txt
```

说明：如果 DRL 依赖在目标环境中不可用，`path_service.py` 应回退到 Dijkstra。只要服务不崩溃、路径可计算、验收通信通过，即可作为基础验收结果。

## 7. 网络规划

推荐 VM 使用两类网卡：

| 网卡类型 | 用途 | 配置建议 |
| --- | --- | --- |
| 管理网卡 | SSH、Git、Web 访问、依赖安装 | NAT、桥接或普通管理网均可 |
| 外部数据面网卡 | 接入真实 SDN 交换机 | 桥接或直通到物理网口，不需要配置业务 IP |

检查 VM 网卡：

```bash
ip addr
ip link
```

外部数据面网卡会被加入 OVS，作为 Mininet 边界交换机端口使用。该网卡不应承担 VM 管理流量。

`acceptance.sh start` 会检查 `<EXTERNAL_INTERFACE>` 是否承载默认路由。如果该接口是 SSH/管理网卡，脚本会拒绝启动，避免把管理网卡加入 OVS 后导致远程连接中断。只有在现场确认该行为是有意设计时，才允许设置：

```bash
export ALLOW_EXTERNAL_INTF_HAS_DEFAULT_ROUTE=1
```

## 8. 验收配置

配置文件：

```text
config/hybrid_acceptance.json
```

通用模板：

```json
{
  "external_interface": "<EXTERNAL_INTERFACE>",
  "controllers": {
    "ports": [6654, 6655, 6656, 6657, 6658, 6659, 6670]
  },
  "hybrid": {
    "external_link_ports": [
      {"dpid": 1, "port": 20}
    ],
    "gateway_ip": "<VIRTUAL_GATEWAY_IP>",
    "gateway_mac": "02:00:00:00:fe:01",
    "real_routes": ["<REAL_NETWORK_CIDR>"]
  },
  "traffic_classes": [
    {
      "name": "task_0",
      "port_start": 1,
      "port_end": 5000,
      "drl_type": 0,
      "route_policy": "min_delay",
      "flow_priority": 30,
      "drl_demand_kbps": 100,
      "drl_duration": 100
    },
    {
      "name": "task_1",
      "port_start": 5001,
      "port_end": 10000,
      "drl_type": 1,
      "route_policy": "max_bandwidth",
      "flow_priority": 20,
      "drl_demand_kbps": 1500,
      "drl_duration": 100
    },
    {
      "name": "task_2",
      "port_start": 10001,
      "port_end": 65535,
      "drl_type": 2,
      "route_policy": "hybrid",
      "flow_priority": 10,
      "drl_demand_kbps": 1500,
      "drl_duration": 100
    }
  ],
  "validation": {
    "virtual_host_name": "<VALIDATION_HOST_NAME>",
    "virtual_host_ip": "<VALIDATION_HOST_IP>",
    "real_host_ip": "<REAL_HOST_IP>",
    "expected_real_switch_dpid": <REAL_SWITCH_DPID>
  }
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `external_interface` | VM 内连接真实 SDN 网络的数据面网卡 |
| `controllers.ports` | 应监听的 Ryu OpenFlow 端口 |
| `hybrid.external_link_ports` | Mininet 静态虚实边界端口 |
| `hybrid.gateway_ip` | 虚拟主机访问真实网段时使用的网关 IP |
| `hybrid.gateway_mac` | 控制器响应虚拟网关 ARP 时使用的 MAC |
| `hybrid.real_routes` | 真实侧主机所在网段 |
| `traffic_classes` | TCP/UDP 端口区间到业务类、路由策略、流表优先级和 DRL `rtype/demand/duration` 的映射 |
| `validation.*` | 自动验收使用的源主机和目标主机 |

以上字段必须按现场拓扑填写，不应沿用旧实验环境中的主机名、IP、端口或网段。

`traffic_classes` 会在 `acceptance.sh start` 时由 `tools/acceptance_config.py` 校验并导出为 `TRAFFIC_CLASSES_JSON`。控制器运行时以该环境变量为准；如果 JSON 中缺少该段，系统使用默认三类端口映射。配置校验会拒绝空列表、重复名称、非法端口、非正优先级、非正 DRL 需求/持续时间，以及当前未开放的 `drl_type=3`。

## 9. 环境变量

建议为每个部署环境维护一个独立环境文件：

```bash
cat > <ENV_FILE> <<'EOF'
export PYTHON_BIN=<PYTHON_FOR_RYU_AND_SERVER>
export PATH_SERVICE_PYTHON=<PYTHON_FOR_PATH_SERVICE>
export MININET_PYTHON=<SYSTEM_PYTHON_WITH_MININET>
export SERVER_AGENT_ROUTE_MODE=hybrid
export EVENTLET_NO_GREENDNS=yes
# 如需非交互 sudo，可设置：
# export SUDO_PASSWORD=<SUDO_PASSWORD>
EOF
```

使用：

```bash
source <ENV_FILE>
```

常见配置：

- `PYTHON_BIN`：运行 `server_agent.py`、控制器启动脚本和验收工具的 Python。
- `PATH_SERVICE_PYTHON`：运行 `drl-or-s/path_service.py` 的 Python。
- `MININET_PYTHON`：系统 Mininet 可用的 Python，通常为 `/usr/bin/python3`。
- `SERVER_AGENT_ROUTE_MODE`：推荐 `hybrid`。

## 10. 真实交换机配置

### 10.1 配置控制器地址

真实交换机需要把 OpenFlow controller 指向主控制器地址：

```text
configure terminal
openflow set controller mgmt-if tcp <CONTROLLER_REACHABLE_IP> 6654
end
openflow write
write
show running-config | include controller
```

期望：

```text
openflow set controller mgmt-if tcp <CONTROLLER_REACHABLE_IP> 6654
```

如果控制器运行在 VM 中，但真实交换机只能访问宿主机 IP，可在宿主机上做端口转发。Windows 示例：

```powershell
netsh interface portproxy add v4tov4 `
  listenaddress=<HOST_REACHABLE_IP> listenport=6654 `
  connectaddress=<VM_MANAGEMENT_IP> connectport=6654

New-NetFirewallRule `
  -DisplayName "DRL ORS OpenFlow 6654" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalAddress <HOST_REACHABLE_IP> `
  -LocalPort 6654
```

验证：

```powershell
netsh interface portproxy show all
Test-NetConnection <HOST_REACHABLE_IP> -Port 6654
netstat -ano | Select-String ':6654'
```

### 10.2 配置真实数据面端口

连接 VM 外部数据面网卡的真实交换机端口必须进入 OpenFlow 数据面：

```text
show interface status
show interface <REAL_SWITCH_PORT>
show running-config interface <REAL_SWITCH_PORT>

configure terminal
interface <REAL_SWITCH_PORT>
openflow enable
exit
end
openflow write
write
show running-config interface <REAL_SWITCH_PORT>
```

期望：

```text
interface <REAL_SWITCH_PORT>
 openflow enable
!
```

如果该端口没有启用 OpenFlow，典型现象是 VM 外部网卡能抓到 ICMP request 发出，但收不到真实主机 reply。

### 10.3 配置真实主机侧端口

真实主机侧端口应满足现场网络规划要求。常见三层口示例：

```text
interface <REAL_HOST_PORT>
 no switchport
 ip address <REAL_GATEWAY_IP>/<PREFIX_LENGTH>
 openflow enable
!
```

验证：

```text
show running-config interface <REAL_HOST_PORT>
show ip interface brief
show ip route
```

## 11. 一键验收流程

进入项目目录：

```bash
cd <PROJECT_DIR>
source <ENV_FILE>
```

清理旧状态：

```bash
./acceptance.sh stop
```

启动：

```bash
./acceptance.sh start
```

健康检查：

```bash
./acceptance.sh health
```

功能覆盖审计：

```bash
./acceptance.sh audit
```

随机负载测试：

```bash
./acceptance.sh load --flows 20 --duration 10 --parallel 5 --seed 1
```

UDP 负载测试：

```bash
./acceptance.sh load --udp --bandwidth 20M --flows 30 --duration 15 --parallel 10 --seed 1
```

生成报告：

```bash
./acceptance.sh report
```

停止：

```bash
./acceptance.sh stop
```

健康检查通过时应覆盖：

- 项目核心功能静态审计，包括 Web 路径高亮、路径重规划关联、手动流表、DRL 路由模式、K 候选路径、流表生命周期、链路故障重规划、虚拟网关和外部主机隔离。
- `6001`、`6009`、`8889` 监听。
- `6654` 到 `6670` 的控制器端口监听。
- `/api/health`、`/api/statistics`、`/api/acceptance/status`、`/api/controllers`、`/api/topo`、`/api/graph?include_flows=0`、`/api/route_sessions` 可访问。
- Web 拓扑图和路径会话一致性审计通过。
- 运行进程环境变量与 JSON 配置一致，包括 `TRAFFIC_CLASSES_JSON`。
- 验收源主机进程存在。
- 验收源主机存在到真实网段的路由。
- 虚拟主机到真实主机 ping 通过。
- 边界交换机存在双向流表。

负载测试不属于健康检查默认流程。它会主动产生并发业务流，可能影响真实交换机和当前演示环境，建议在健康检查通过后单独执行。

## 12. Web/API 验证

Web：

```text
http://<SERVER_AGENT_IP>:6009
```

API：

```bash
curl -s http://127.0.0.1:6009/api/health
curl -s http://127.0.0.1:6009/api/statistics
curl -s http://127.0.0.1:6009/api/acceptance/status
curl -s http://127.0.0.1:6009/api/controllers
curl -s http://127.0.0.1:6009/api/route_sessions
```

`/api/acceptance/status` 通过时应返回：

```json
{
  "status": "ready",
  "issues": []
}
```

端口业务分类验证时，访问 `/api/route_sessions`，确认会话中包含以下字段：

```json
{
  "task_type": "task_1",
  "route_policy": "max_bandwidth",
  "drl_type": 1,
  "drl_demand_kbps": 1500,
  "drl_duration": 100
}
```

也可以查看 `logs/server_agent.log`，路径请求日志应包含 `task=... policy=... drl_type=... demand=... duration=...`。

## 13. 手工分步启动

当一键启动失败时，按以下顺序手工启动。

启动路径服务：

```bash
cd <PROJECT_DIR>
source <ENV_FILE>
$PATH_SERVICE_PYTHON drl-or-s/path_service.py --topo <TOPO_NAME> --port 8889 --model <MODEL_PATH>
```

启动汇聚服务：

```bash
cd <PROJECT_DIR>
source <ENV_FILE>
$PYTHON_BIN server_agent.py hybrid
```

启动控制器：

```bash
cd <PROJECT_DIR>
source <ENV_FILE>
$PYTHON_BIN -u start_controllers_test.py start -n
```

启动 Mininet 拓扑：

```bash
cd <PROJECT_DIR>
source <ENV_FILE>
sudo -E $MININET_PYTHON -u testbed/creat_test_topo.py <EXTERNAL_INTERFACE>
```

后台验收模式：

```bash
sudo -E $MININET_PYTHON -u testbed/creat_test_topo.py <EXTERNAL_INTERFACE> --hold
```

## 14. 手工测试命令

### 14.1 控制面端口

Linux：

```bash
ss -ltnp | egrep ':6001|:6009|:8889|:6654|:6655|:6656|:6657|:6658|:6659|:6670'
ss -tnp '( sport = :6654 or dport = :6654 )'
```

Windows 宿主机：

```powershell
netstat -ano | Select-String ':6654'
```

### 14.2 Mininet 源主机路由

```bash
pid=$(ps -eo pid=,args= | awk -v host="<VALIDATION_HOST_NAME>" '{$1=$1; pid=$1; sub(/^[^ ]+[ ]+/, "", $0); if ($0 == "bash --norc --noediting -is mininet:" host) {print pid; exit}}')
sudo mnexec -a "$pid" ip route
```

期望存在：

```text
<REAL_NETWORK_CIDR> via <VIRTUAL_GATEWAY_IP> dev <HOST_INTERFACE>
```

### 14.3 虚拟主机到真实主机连通性

```bash
sudo mnexec -a "$pid" ping -c 3 -W 1 <REAL_HOST_IP>
```

通过标准：

```text
3 packets transmitted, 3 received, 0% packet loss
```

### 14.4 外部网卡抓包

```bash
sudo timeout 8 tcpdump -n -i <EXTERNAL_INTERFACE> 'icmp or arp' -vv
```

判断：

- 看不到 request：检查 Mininet 路由、控制器流表、边界 OVS 端口。
- 看到 request 但没有 reply：检查真实交换机端口、真实主机、真实侧回程。
- request/reply 都存在但 ping 失败：检查回程流表和目的 MAC 改写。

### 14.5 OVS 端口与流表

```bash
sudo ovs-vsctl show
sudo ovs-vsctl get Interface <EXTERNAL_INTERFACE> ofport
sudo ovs-ofctl -O OpenFlow13 dump-flows <BOUNDARY_SWITCH>
sudo ovs-ofctl -O OpenFlow13 dump-flows <VALIDATION_SWITCH>
```

虚实通信通过后，应至少存在：

- 虚拟主机到真实主机的正向流。
- 真实主机到虚拟主机的反向流。
- 边界交换机上输出到 `<EXTERNAL_PORT>` 的流。
- 边界交换机上从 `<EXTERNAL_PORT>` 返回内部拓扑的流。

## 15. 自动化测试

单元和集成测试：

```bash
python -m pytest \
  tests/test_acceptance_config.py \
  tests/test_acceptance_health.py \
  tests/test_acceptance_report.py \
  tests/test_acceptance_scripts.py \
  tests/test_acceptance_web_status.py \
  tests/test_web_acceptance_status.py \
  tests/test_external_link_ports_config.py \
  tests/test_path_service_decision_metadata.py \
  tests/test_server_agent_startup.py \
  tests/test_server_agent_graph_lock.py \
  tests/test_server_agent_drl_metadata.py \
  tests/test_server_agent_shadow_mode.py \
  tests/test_web_api_performance.py \
  tests/test_web_performance_architecture.py \
  tests/test_delivery_scripts.py \
  -q
```

配置校验：

```bash
python tools/acceptance_config.py --config config/hybrid_acceptance.json
python tools/acceptance_config.py --config config/hybrid_acceptance.json --shell-env
```

功能覆盖审计：

```bash
python tools/acceptance_feature_audit.py
```

随机负载测试：

```bash
python tools/mininet_load_test.py --flows 20 --duration 10 --parallel 5 --seed 1
```

端口业务分类验证：

```bash
# 从 Mininet 主机或真实业务端发起 TCP/UDP 流量，目的端口分别落入三段范围：
# 4000  -> task_0 / rtype 0 / demand 100Kbps
# 7000  -> task_1 / rtype 1 / demand 1500Kbps
# 12000 -> task_2 / rtype 2 / demand 1500Kbps
curl -s http://127.0.0.1:6009/api/route_sessions
tail -n 200 logs/server_agent.log | grep -E 'task_|drl_type|demand|duration'
```

端到端验收：

```bash
./acceptance.sh stop
./acceptance.sh start
./acceptance.sh health
./acceptance.sh audit
./acceptance.sh load --flows 20 --duration 10 --parallel 5 --seed 1
./acceptance.sh report
```

退出码含义：

| 退出码 | 含义 |
| ---: | --- |
| `0` | 验收通过 |
| `1` | 控制面或配置失败 |
| `2` | 数据面有风险，需继续定位 |

## 16. 日志

常用日志：

```bash
tail -f logs/path_service.log
tail -f logs/server_agent.log
tail -f logs/server_agent.stdout.log
tail -f logs/controllers.log
tail -f logs/ryu_controller_6654.log
tail -f logs/mininet_topology.log
```

常用检索：

```bash
grep -E '<REAL_HOST_IP>|<VALIDATION_HOST_IP>|request_path|hybrid|dijkstra|fallback' logs/server_agent.log
grep -E '<REAL_HOST_IP>|PACKET-WATCH|主机链路|内部链路|外部链路' logs/ryu_controller_6654.log
```

## 17. 故障定位

| 现象 | 优先检查 |
| --- | --- |
| 真实交换机未连接控制器 | controller IP、宿主机端口转发、防火墙、VM `6654` 监听 |
| 外部网卡无链路 | VM 桥接/直通设置、物理线缆、真实交换机端口状态 |
| 验收源主机路由缺失 | `hybrid.real_routes`、虚拟网关配置、Mininet 启动日志 |
| ping 无 request | Mininet 路由、proxy ARP、边界端口、控制器流表 |
| 有 request 无 reply | 真实交换机端口 OpenFlow、真实主机状态、真实侧回程路由 |
| 流表不完整 | 先触发一次 ping，再检查 `server_agent` 路径会话和 OVS 流表 |
| Web 状态不是 ready | 查看 `/api/acceptance/status` 的 `issues` 字段 |

## 18. 验收清单

部署完成后按顺序确认：

- [ ] Linux/VM 已安装 Python、Mininet、Open vSwitch、Ryu。
- [ ] 项目依赖安装完成。
- [ ] `config/hybrid_acceptance.json` 已替换为现场参数。
- [ ] VM 外部数据面网卡存在且接入真实 SDN 网络。
- [ ] 真实交换机 controller 指向 `<CONTROLLER_REACHABLE_IP>:6654`。
- [ ] 真实交换机数据面接入口已 `openflow enable`。
- [ ] 真实主机已开机，IP 和网关符合现场规划。
- [ ] `./acceptance.sh start` 成功。
- [ ] `./acceptance.sh health` 返回通过。
- [ ] `./acceptance.sh report` 生成报告。
- [ ] Web 页面可访问，验收状态为 ready。

## 19. 现场演示顺序

建议按以下顺序演示：

1. 说明真实交换机、VM 外部网卡、真实主机的连接关系。
2. 展示 `config/hybrid_acceptance.json` 中的现场参数。
3. 展示真实交换机 controller 和数据面端口配置。
4. 执行 `./acceptance.sh stop && ./acceptance.sh start`。
5. 执行 `./acceptance.sh health`。
6. 手工执行一次 `<VALIDATION_HOST> -> <REAL_HOST_IP>` ping。
7. 展示边界交换机和验证交换机的关键流表。
8. 执行 `./acceptance.sh report` 并打开报告。
9. 打开 Web 页面，确认验收状态。

## 20. 责任边界

P0 验收必须保证：

- 控制器端口监听正确。
- 真实交换机能连接控制器。
- 静态虚实边界配置正确。
- 真实交换机接入口进入 OpenFlow 域。
- 虚拟主机到真实主机通信通过。
- 健康检查和验收报告可复现。

P0 验收不强制要求：

- VM 边界 LLDP 自动发现成功。
- DRL 每次都替代 Dijkstra。
- 宿主机、VM 管理网、真实业务网处于同一网段。

## 21. 环境专用网口配置说明

虚实通信不能跨环境复用网口名。当前仓库保留：

- `config/hybrid_acceptance.json`：当前默认验收配置，随当前 VM 使用 `ens34`。
- `config/hybrid_acceptance.vm.json`：VM 模板，`external_interface` 为 `ens34`。
- `config/hybrid_acceptance.server.json`：服务器模板，`external_interface` 暂保留为 `eno1`，服务器可访问后必须重新用 `ip link`、`ip route show default`、`sudo ovs-vsctl show` 复核。

`./acceptance.sh start` 只负责按配置启动。`./acceptance.sh health` 会进一步检查配置和运行时是否一致：

- 配置网口是否存在。
- 配置网口是否没有承载默认路由。
- 配置网口是否实际接入 `hybrid.external_link_ports` 指定的 OVS 边界交换机。
- 配置网口在 OVS 中的 `ofport` 是否等于静态边界端口。

因此，如果 VM 实际运行的是 `ens34 -> s1:port20`，但配置文件写成 `eno1`，健康检查会失败。这用于防止配置漂移被误判为虚实通信通过。

## 22. 虚实链路延迟来源

内部虚拟链路仍可使用控制器 Echo 与 LLDP 估算 delay。虚实边界链路不再把 LLDP 作为验收依据，因为 VM、宿主机、物理交换机或 OpenFlow 域边界都可能影响 LLDP 透传。

虚实边界现在使用两层证据：

- 运行配置：`hybrid.external_link_metrics` 为外部边界端口提供 `delay_ms`、`bandwidth_mbps`、`loss_percent` 和 `source`，控制器在识别到该端口时使用这些 metric 作为边界链路权重。
- 主动测量：`./acceptance.sh health` 从验证主机主动 ping 真实主机，解析 RTT、丢包率和估算单向延迟，输出 `virtual_real_latency`。该值是端到端测量，不等同于单条物理链路精确时延。

当现场未知真实延迟时，`delay_ms` 可以保留为 `0` 或现场保守估计；最终验收报告应以 `virtual_real_latency` 的实测 RTT 作为展示证据。
