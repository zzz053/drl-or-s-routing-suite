# DRL-OR-S 项目阶段性工作整理

日期：2026-05-26

## 1. 本阶段目标

本阶段主要围绕两条主线推进：

1. 让 `hybrid` 模式下的跨域路由真正进入 `server_agent -> path_service -> DRL` 路径规划链路，而不是仍然绕回本地最短路径。
2. 提升 Web 可视化的可信度和可用性，让拓扑、路径会话、流表、链路信息能够稳定、清晰、低卡顿地反映系统状态。

目前系统已经能够在跨域通信时触发 DRL/path_service 路径规划，并在 Web 中展示 route session、路径高亮、交换机真实编号、链路信息和流表状态。

## 2. 已完成工作

### 2.1 DRL hybrid 路由链路修复

之前的问题是：即使以 `hybrid` 模式启动，跨域 ping 时也经常看不到 `path_service` 的路径规划日志。排查后发现，远端主机信息被广播到其他控制器后，被写进了本地 `host_to_sw_port`，导致控制器误以为目的主机是本地域内主机，于是直接走本地 SPF，不再发送 `path_request`。

已修复：

- 远端主机不再写入本地 `host_to_sw_port`。
- 新增远端主机观测状态，用于记录但不参与本地路径判断。
- 目的主机不在本地时，明确触发 `path_request`。
- `server_agent` 在 `hybrid` 模式下调用 `path_service`，并保留 fallback。
- 增加关键日志，方便确认是否进入 DRL 链路。

相关提交：

```text
c252a99 Fix cross-domain DRL path request flow
```

当前跨域路径流程：

```text
PacketIn
-> 控制器判断目的主机不在本地
-> 发送 path_request 到 server_agent
-> server_agent 生成候选路径并调用 path_service
-> path_service 返回路径和 DRL 元信息
-> hybrid 模式优先使用有效 DRL 路径，失败回退 Dijkstra
-> 控制器安装流表并释放 pending packet
```

### 2.2 路径流生命周期和过期清理

之前 Web 中停止 `h20 ping h40` 后，相关流仍然长时间存在。根因是自动下发的路径流默认 `idle_timeout=0/hard_timeout=0`，交换机不会主动删除，也就不会触发 `flow_removed`。

已修复：

- 自动路径流默认加入 `ROUTE_FLOW_IDLE_TIMEOUT`，默认 15 秒。
- 非 table-miss 流下发时带 `OFPFF_SEND_FLOW_REM`。
- 控制器处理 `EventOFPFlowRemoved`。
- 控制器清理本地 `switch_flow_stats` 和相关 route session。
- `server_agent` 收到 `flow_removed` 后清理缓存流表和 route session。
- Web 通过轻量刷新看到流和 route session 消失。

相关提交：

```text
b2b0371 Improve route flow lifecycle and meeting notes
```

### 2.3 首包时序和 OpenFlow Barrier

用户测试时感觉 “DRL 还没有规划完，包就已经发出去了”。排查后确认，控制器在等待跨域路径时会把首包放入 pending 队列，并不会立即 packet_out。但仍存在一个更细的时序风险：其他控制器的流表可能还没有真正被交换机应用，请求控制器就释放了 pending packet。

已修复：

- 控制器安装跨域路径流后发送 BarrierRequest。
- 收到 BarrierReply 后才认为本控制器流表安装完成。
- `path_install_ack` 携带 `barriers_ok`。
- `server_agent` 等待非请求控制器 ACK 后，再通知请求控制器释放 pending packet。

这降低了跨域首包在路径未完全安装时提前发送的风险。

### 2.4 Web 性能优化

Web 之前卡顿明显，主要原因是前端每隔几秒全量刷新拓扑、重建节点和边、重新布局，同时还把流表等高频数据混进图数据。

已完成优化：

- 新增 `web_state_store.py`，维护 Web 快照版本。
- `/api/graph` 返回缓存快照，不再每次重算 NetworkX 图。
- 前端从全量 `clear + add` 改成 DataSet 增量同步。
- 拓扑结构和链路指标拆分，`delay/bw/loss/flow_count` 不再触发整图重绘。
- 只在拓扑结构变化时重新布局。
- 鼠标 hover 不再通过 `edges.update()` 修改图数据，避免移动鼠标时抖动。
- route session 高亮只更新差集，避免扫全图。
- 首页加 `Cache-Control: no-store`，防止浏览器继续跑旧版 JS。

相关提交：

```text
ad27f05 Improve web graph refresh performance
dba447c Prioritize web graph interactions
cd9e1ea Decouple route highlight from hover updates
d9920a2 Disable web UI caching
```

### 2.5 Route Sessions 路径高亮修复

Web 中点击 Route Sessions 面板后，最初存在两类问题：

- 点击后路径没有高亮。
- 拓扑图反复缩放或抖动。

根因包括：

- 前端使用不稳定的 edge index 生成边 ID，后端边数组顺序变化后，高亮找不到对应边。
- 拓扑刷新中自动 `network.fit()` 会抢用户视角。
- hover 事件和 vis.js 内建选中效果同时改边样式，导致移动鼠标时图抖。
- 高亮颜色与原本跨域链路颜色太接近，不够明显。

已修复：

- switch link 使用稳定 ID，不再依赖数组下标。
- 初始拓扑只自动 fit 一次。
- route session 点击改成事件委托。
- 高亮同时使用样式更新和 `network.selectNodes/selectEdges` 兜底。
- 高亮样式改成青绿色粗线、虚线节奏和发光阴影。

相关提交：

```text
172daf7 Fix route session highlight mapping
7dc833e Stabilize route session highlighting
15eece9 Increase route highlight contrast
```

### 2.6 Web 编号和信息一致性修复

最近发现的新问题是：图上的 `SW32` 可能对应真实 DPID 31，Route Session 中显示 31，右侧面板也显示 31，看起来完全对不上。

根因是前端之前把 “第几个被渲染的交换机” 当作 `SWxx` 显示，而不是用真实 DPID。

已修复：

- 拓扑图交换机标签改成真实 DPID，例如 `SW31`。
- Route Sessions 面板路径改成 `Host(...) -> SW31 -> SW28 -> ...`。
- 链路右侧面板显示 `Source Switch: SW31`，同时保留 `Source DPID: 31`。
- 交换机右侧面板标题、副标题和 `Display Label` 与图上标签一致。

相关提交：

```text
d552573 Align web switch identity labels
```

### 2.7 一键整理拓扑图

Web 中拖动交换机后，布局可能变乱。当前已经新增顶部按钮 `整理拓扑`。

点击后会：

- 清空手动拖动产生的交换机坐标。
- 删除浏览器中的 `hydrateCompactSwitchPositions`。
- 基于当前拓扑重新执行自动域布局。
- 保留当前 route session 高亮。
- 自动 fit 到完整视野。

这个方案比固定一套坐标更合适，因为拓扑、控制器、交换机、链路数量可能变化。重新计算当前拓扑的默认布局，比保存死坐标更稳。

相关提交：

```text
1cc3784 Add topology auto arrange control
```

### 2.8 自动一致性审计工具

为了减少人工一点点查问题，新增了 Web 一致性审计脚本：

```bash
python tools/web_consistency_audit.py --base-url http://127.0.0.1:6009
```

当前可以自动检查：

- Web graph 中是否存在 switch 节点。
- switch link 两端是否都是合法交换机。
- route session 的 `switch_path` 是否都存在于当前拓扑。
- route session 中每一跳交换机之间是否真的存在 `switch_link`。

这个脚本已经用于服务器验证：

```text
Web consistency audit ok: 55 nodes, 176 edges, 0 route sessions
```

后续可以继续扩展为：

- 检查 Route Sessions 是否能找到可高亮边。
- 检查 Web 标签是否和真实 DPID 一致。
- 检查右侧面板显示是否和图上节点一致。
- 检查 DRL 元数据是否完整展示。

## 3. 当前系统状态

当前 GitHub 和 Linux 服务器已同步到：

```text
1cc3784 Add topology auto arrange control
```

当前 Web：

```text
http://10.5.1.163:6009
```

最近一次服务器检查：

```text
controllers=7
graph_nodes=55
graph_edges=176
status=ok
```

本地验证：

```text
pytest tests -q
59 passed

python -W error -m py_compile server_agent.py web_api.py web_state_store.py web_ui_html.py tools/web_consistency_audit.py
通过

node -c extracted_web_ui_script.js
通过
```

服务器验证：

```text
manual web tests ok
web_consistency_audit 通过
```

## 4. 当前需要注意的问题

### 4.1 path_service 还不是完整的 K 候选 DRL 选择器

现在 `server_agent` 已经能生成 K 条候选路径，并把候选路径、链路状态、业务类型等信息发送给 `path_service`。但是 `path_service` 内部还没有完全升级成 “DRL 对 K 条候选路径逐条打分并选择最优路径” 的形式。

组会中建议表述为：

> 当前已经打通 hybrid 模式下的 DRL/path_service 在线调用链路，并预留 K 候选路径接口；下一步需要将 path_service 内部升级为真正的候选路径评分与选择模型。

不建议表述为：

> DRL 已经完整实现了基于 K 条候选路径的最优路径选择。

### 4.2 Web 已明显优化，但仍是轮询架构

Web 当前已经减少了全量重绘和图抖动，但仍然依赖 HTTP 轮询，不是 SSE/WebSocket 推送。因此流表过期、route session 删除、链路变化到页面展示之间仍可能有几秒延迟。

### 4.3 Mininet/Ryu 重启会清空 route session

每次重启 `server_agent`、控制器或拓扑后，Route Sessions 面板会清空，需要重新跑跨域流量生成会话。

### 4.4 自动审计还处于第一阶段

`web_consistency_audit.py` 已经能检查路径和拓扑的一致性，但还不能完全替代人工 UI 检查。后续应扩展成更完整的 Web 自检工具。

## 5. 后续工作方向

### 5.1 完成真正的 DRL 候选路径选择器

下一阶段核心任务：

- server_agent 继续生成 K 条候选路径。
- 每条候选路径提取特征：hop count、delay、loss、min bandwidth、链路状态、业务类型。
- path_service 对候选路径输出 score 或 selected index。
- 返回 `selected_candidate_id`、`candidate_scores`、`decision_source=drl_candidate_selector`。
- hybrid 模式只安装通过拓扑校验和策略校验的路径。

### 5.2 增强 Web 中 DRL 决策可观测性

建议在 Web route session 面板中展示：

- route mode。
- `decision_source`。
- `model_used`。
- candidate count。
- fallback reason。
- DRL compute time。
- selected candidate id。
- path score。

### 5.3 把 Web 轮询升级为事件推送

后续可以考虑：

- SSE 或 WebSocket 推送 topology version、flow version、route session version。
- 前端收到事件后只刷新受影响的交换机或 route session。
- 减少轮询延迟和无效 API 请求。

### 5.4 扩展自动审计流程

建议把 “人工发现的问题” 逐步沉淀成自动检查：

- 路径 hop 是否存在。
- 每一跳是否有 switch link。
- 图上标签是否等于真实 DPID。
- route session 是否能映射到可高亮边。
- 点击节点后右侧面板 ID 是否一致。
- Web 返回的 graph version 是否随拓扑变化正确更新。

最终可以形成一键检查：

```bash
python tools/web_consistency_audit.py --base-url http://127.0.0.1:6009
```

并扩展为：

```bash
python tools/run_web_diagnostics.py --base-url http://127.0.0.1:6009 --include-ui-rules
```

### 5.5 完善实验自动化

建议增加一套 Linux 实验脚本：

- 一键启动 hybrid。
- 自动执行指定跨域 ping/iperf。
- 抓取 `server_agent.log`、`path_service.log`、`ryu_controller_*.log`。
- 输出本次路径来源、DRL 是否使用、最终路径、流表过期状态。
- 自动运行 Web 一致性审计。

## 6. 组会汇报建议顺序

建议按下面逻辑讲：

1. 先说明目标：本阶段主要解决 DRL 跨域路径规划是否真正生效，以及 Web 展示是否可信、可用。
2. 再讲 DRL 链路问题：发现远端主机污染本地 host 表，导致跨域流量绕过 DRL；修复后 hybrid 能进入 `server_agent -> path_service`。
3. 接着讲路径流生命周期：停止 ping 后旧流不消失，根因是自动路径流永久存在；现在加入默认 idle timeout 和 FlowRemoved 清理。
4. 再讲 Web 性能：从全量重建改为版本快照、增量更新、指标与结构解耦。
5. 然后讲 Web 正确性：修复 route session 高亮、鼠标抖动、编号不一致、右侧面板信息不一致。
6. 最后讲可用性和自动化：新增一键整理拓扑、Web 一致性审计脚本，减少人工排查成本。
7. 收尾强调下一步：把 path_service 升级成真正的 K 候选路径 DRL selector，并扩展自动诊断工具。

## 7. 一句话总结

本阶段完成了从 “DRL 链路能否真正触发” 到 “Web 是否可信展示” 的一轮系统性修复：跨域 hybrid 路由现在能进入 path_service，路径流具备生命周期管理，Web 性能和交互明显改善，并开始建立自动审计工具，为后续实现真正的 K 候选 DRL 路径选择器打基础。
