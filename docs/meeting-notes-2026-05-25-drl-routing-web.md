# DRL-OR-S 项目阶段性整理

日期：2026-05-25

## 1. 当前目标

本阶段围绕“让 DRL 真正进入跨域路径规划链路，并让 Web 展示的流表/路径状态可信”展开。核心目标有三个：

- 跨域通信时，控制器不再绕过 `server_agent -> path_service -> DRL` 链路。
- Web 中展示的路径会话和交换机流表尽量接近真实运行状态。
- DRL 不稳定或不可用时，系统仍能通过 Dijkstra/SPF fallback 保持可用。

## 2. 已完成修改

### 2.1 DRL 路由模式与启动方式

已支持四种路由模式：

- `spf`：只使用 server_agent 本地 Dijkstra/SPF。
- `shadow`：调用 DRL 作为旁路建议，但实际下发 fallback 路径。
- `hybrid`：优先使用校验通过的 DRL/path_service 路径，失败回退 Dijkstra。
- `drl`：强制使用 DRL，仅适合实验。

现在可以通过命令行直接启动：

```bash
python3 server_agent.py hybrid
python3 server_agent.py shadow
python3 server_agent.py spf
python3 server_agent.py drl
```

命令行参数优先级高于 `DRL_ROUTE_MODE` 环境变量。

### 2.2 DRL 跨域路径请求链路修复

之前发现一个关键问题：`server_agent` 广播远端主机信息后，子控制器会把远端主机写入本地 `host_to_sw_port`，导致 `packetin_ip.py` 误认为目标主机是本域已知主机，从而直接本地算路并下发流表，绕过 DRL。

已修复：

- 远端主机现在写入 `remote_hosts`，不再污染本地 `host_to_sw_port`。
- 跨域目的主机不在本地时，会明确触发 `_request_path()`。
- 增加了关键日志：
  - `[PathRequest] dst_not_local`
  - `[PathRequest] send_to_root`
  - `[DRL] request_path route_mode=...`
  - `[DRL] path_service response ...`
  - `path_service` 侧打印 route mode、candidate 数量、task、policy。

这部分已提交并推送到 GitHub：

```text
c252a99 Fix cross-domain DRL path request flow
```

### 2.3 K 条候选路径与 DRL 调用接口

`server_agent` 已经具备：

- 根据当前全局拓扑生成 K 条候选路径。
- 将候选路径、链路状态、业务类型、策略等信息发送给 `path_service`。
- 接收 `path_service` 返回的路径、`decision_source`、`model_used`、`fallback_reason`、`candidate_count` 等元数据。
- 在 `hybrid` 模式下优先采用校验通过的 DRL/path_service 路径，失败回退 fallback。

需要注意：当前 `path_service.py` 虽然能接收 `candidates`，但还没有真正实现“DRL 在 K 条候选路径中打分选择最优路径”。目前更准确的说法是：系统已经把 K 候选接口接入了 DRL 服务，但 path_service 内部仍主要使用原有 DRL/SHR/Dijkstra 计算逻辑。

### 2.4 Web 性能与数据刷新优化

Web 之前比较卡，主要原因是全量拓扑刷新时携带了较重的流表数据，并且图更新频繁。

已做优化：

- `/api/graph?include_flows=0` 默认不再返回完整流表。
- 交换机流表改为选中交换机后按需请求 `/api/switch/<id>/flows`。
- 当前选中交换机每 3 秒轻量刷新流表。
- route sessions 独立请求 `/api/route_sessions`。
- 图拓扑通过 signature 判断是否需要更新，减少无意义重绘。

### 2.5 流表生命周期与 Web 自动删除

之前已经加入：

- 自动下发的非 table-miss 流表带 `OFPFF_SEND_FLOW_REM`。
- 控制器处理 `EventOFPFlowRemoved`。
- 控制器收到 `flow_removed` 后清理本地 `switch_flow_stats` 和受影响的 `route_sessions`。
- `server_agent` 收到 `flow_removed` 后清理缓存流表和 route sessions。
- Web 通过定期刷新选中交换机流表看到删除结果。

今天进一步发现：自动路径流虽然设置了 `SEND_FLOW_REM`，但默认 `idle_timeout=0/hard_timeout=0`，停止 ping 后交换机不会主动删除流，所以 Web 仍然显示该流。

已修改：

- 新增 `ROUTE_FLOW_IDLE_TIMEOUT`，默认 15 秒。
- 自动路径流默认带 `idle_timeout=15`。
- 手动 Web 下发流仍使用表单中的 `idle_timeout/hard_timeout`，不受默认路径流超时影响。
- flow stats 返回中加入 `idle_timeout` 和 `hard_timeout`，后续 Web 可以展示。

当前这部分已在本地和 Linux 服务器工作树中修改，但还未提交 Git。

### 2.6 跨域首包转发时序

用户测试中感觉“DRL 还没运行完包就发出去了”。代码排查结果：

- 跨域目的主机不在本地时，首包会放入 `_pending_path_packets`。
- 控制器发送 path_request 后直接 return，不会马上 packet_out。
- 真正发送队列包发生在 path_response 返回并安装本域流表之后。

但这里存在一个更细的时序风险：首跳控制器可能在其他域控制器流表还没完全被交换机应用时，就放出队列首包。

已修改：

- 控制器安装跨域路径流表后发送 OpenFlow BarrierRequest。
- 收到 BarrierReply 后才认为本控制器本域流表安装完成。
- `path_install_ack` 携带 `barriers_ok`。
- `server_agent` 只有在非请求控制器 ACK 且 `barriers_ok=True` 时，才认为其他域安装完成。
- 请求控制器在等待其他域 ACK 后才收到 path_response 并放出首包。

## 3. 当前架构流程

### 3.1 正常 hybrid 跨域路径流程

```text
Mininet 主机发包
-> 子控制器收到 PacketIn
-> packetin_ip.py 判断目标主机不在本地 host_to_sw_port
-> 控制器发送 path_request 给 server_agent
-> server_agent 生成 fallback 路径和 K 条候选路径
-> server_agent 调用 path_service
-> path_service 返回 DRL/SHR/Dijkstra 计算结果和元数据
-> server_agent 在 hybrid 模式下选择 DRL/path_service 路径或 fallback
-> server_agent 先通知非请求控制器安装流表
-> 非请求控制器安装流表并等待 BarrierReply 后 ACK
-> server_agent 再通知请求控制器
-> 请求控制器安装本域流表，等待 BarrierReply
-> 请求控制器释放 pending packet
```

### 3.2 流过期与 Web 删除流程

```text
路径流量停止
-> 交换机 idle_timeout 到期
-> 交换机发送 FlowRemoved
-> 控制器 EventOFPFlowRemoved
-> 控制器清理 switch_flow_stats / route_sessions
-> 控制器上报 flow_removed 到 server_agent
-> server_agent 清理缓存 flow table / route_sessions
-> Web 下一次刷新 selected switch flows / route_sessions
-> 页面中对应流和路径会话消失
```

## 4. 验证情况

本地验证：

```text
pytest tests -q
35 passed

python -m py_compile controller.py common_config.py server_agent.py web_api.py packetin_ip.py
通过
```

Linux 服务器验证：

```text
ryu_drl_s 环境 py_compile
通过

直接执行 flow lifecycle 测试函数
通过
```

服务器 `ryu_drl_s` 环境中没有安装 `pytest`，所以不能直接运行 `pytest tests -q`。这不是代码失败，是测试依赖缺失。

## 5. 需要注意的问题

### 5.1 旧流不会自动变成超时流

已经下发到交换机里的旧永久流不会因为代码更新而改变。需要重启控制器/拓扑，或者手动清理旧流，之后新下发的路径流才会带 `idle_timeout`。

默认停止 ping 后，Web 中流消失时间大约是：

```text
ROUTE_FLOW_IDLE_TIMEOUT + Web 刷新间隔
默认约 15s + 3s
```

### 5.2 path_service 还不是严格的“K 候选 DRL 选择器”

当前接口层已经具备 K 候选路径输入，但模型内部还没有真正按候选路径的 delay/loss/bw 做打分选择。组会汇报时建议表述为：

> 已完成 DRL 在线路径规划链路接入和 K 候选接口预留，hybrid 模式可以调用 path_service 并安装校验通过的路径；下一步需要把 path_service 内部升级为真正的候选路径选择器。

不要表述为：

> DRL 已经完整实现了基于 K 条候选路径的最优路径选择。

### 5.3 ICMP/ping 的流识别粒度较粗

目前默认任务分类主要基于 TCP/UDP 端口。`ping` 是 ICMP，没有端口，通常走 `default` 业务类型和默认优先级。后续如果要区分 ICMP 实验流，需要给 ICMP 增加更明确的 match 和业务分类策略。

### 5.4 FlowRemoved 依赖交换机支持和流表超时

只有设置了 timeout 且带 `OFPFF_SEND_FLOW_REM` 的流，才会稳定触发 `flow_removed`。永久流、table-miss 流、或部分手动流如果 timeout 为 0，不会自动过期。

### 5.5 Web 展示仍是轮询模式

当前 Web 依赖 HTTP 轮询，不是 WebSocket/SSE 推送。因此 FlowRemoved 到页面消失之间仍会有刷新延迟。

## 6. 后续修改方向

### 6.1 真正实现 DRL 候选路径选择器

建议下一阶段重点做：

- `server_agent/path_service` 继续生成 K 条候选路径。
- 每条候选路径提取特征：hop_count、total_delay、min_bw、loss、拥塞状态、链路 down 状态、业务类型。
- DRL 模型输出候选路径 index 或每条候选路径 score。
- path_service 返回 `selected_candidate_id`、`candidate_scores`、`decision_source=drl_candidate_selector`。
- hybrid 模式只安装通过拓扑校验和策略校验的候选路径。

### 6.2 完善 DRL 可观测性

建议 Web 和日志展示：

- 当前 route mode。
- 是否调用 path_service。
- `model_used=true/false`。
- fallback 原因。
- 候选路径数量。
- 最终路径来源：`drl_model` / `drl_candidate_selector` / `dijkstra` / `shadow_fallback`。
- DRL 推理耗时。

### 6.3 Web 从轮询升级为事件推送

当前 Web 轮询已经比之前轻很多，但后续可以改成：

- 流表变化、路径会话变化、链路 up/down 通过 SSE 或 WebSocket 推送。
- Web 收到事件后只刷新受影响的交换机或 route session。
- 减少全量轮询带来的卡顿。

### 6.4 完善流生命周期策略

建议区分不同流类型：

- ICMP/ping 实验流：较短 idle timeout，例如 5-15 秒。
- TCP/UDP 业务流：可根据业务类型设置不同 timeout。
- 手动 Web 流：由用户显式设置 timeout，默认可以提示永久或临时。
- DRL 实验流：记录路径决策元数据，方便过期后分析。

### 6.5 加强 Linux 实验自动化

建议补充一套脚本：

- 一键启动 hybrid。
- 自动执行指定跨域 ping/iperf。
- 自动抓取 `server_agent.log`、`path_service.log`、`ryu_controller_*.log` 中的关键标记。
- 自动输出本次路径来源、是否 DRL、最终路径、流表过期时间。

## 7. 组会汇报建议话术

可以按下面顺序讲：

1. 本阶段先解决了 DRL 没有真正进入跨域路径规划的问题，根因是远端主机信息污染了本地域内主机表，导致控制器绕过 root/server_agent。
2. 修复后，跨域目的主机不在本地时会明确发 path_request，server_agent 在 hybrid 模式下调用 path_service，并保留 fallback。
3. Web 卡顿问题通过拆分拓扑刷新和流表按需刷新缓解，route sessions 也独立展示。
4. 最近发现停止 ping 后 Web 仍显示旧流，根因是自动路径流默认永久。现在已加默认 idle timeout 和 flow_removed 清理链路。
5. 对首包时序增加了 OpenFlow barrier，避免路径响应后流表尚未真正安装就释放 pending packet。
6. 当前还要继续做的是把 path_service 内部升级为真正的 K 候选路径 DRL selector，并增强 Web 上的 DRL 决策可观测性。

## 8. 当前代码状态

- GitHub 上最新已推送提交：`c252a99 Fix cross-domain DRL path request flow`。
- 今天关于路径流超时和 barrier 的修改已经在本地和 Linux 服务器工作树中，但尚未提交。
- 当前未提交文件：
  - `common_config.py`
  - `controller.py`
  - `server_agent.py`
  - `tests/test_controller_flow_lifecycle.py`
