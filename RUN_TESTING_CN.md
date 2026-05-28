# DRL-OR-S Routing Suite 运行与测试说明

本文档说明如何启动、检查和测试 `drl-or-s-routing-suite` 交付项目。

该目录可以从父项目中单独拷贝出去运行。交付目录内已经包含控制器、Web、Military Mininet 拓扑、DRL path_service 运行代码、Military 拓扑数据和 Military 模型权重。

## 1. 目录说明

交付目录为：

```text
drl-or-s-routing-suite/
```

关键文件如下：

- `server_agent.py`：根控制器服务，监听控制器连接，提供 Web/API。
- `controller.py`：Ryu 控制器应用。
- `start_controllers_test.py`：启动 Military 验收拓扑对应的 7 个 Ryu 控制器。
- `drl-or-s/path_service.py`：DRL 路径计算服务。
- `drl-or-s/a2c_ppo_acktr/`：DRL 推理模型代码。
- `drl-or-s/net_env/`：DRL 拓扑环境代码。
- `drl-or-s/model/Military_mininet/`：Military 模型权重，包含 `agent0.pth` 到 `agent46.pth`。
- `drl-or-s/topology/Military/`：DRL 推理使用的 Military 拓扑数据。
- `testbed/creat_test_topo.py`：Military 验收拓扑。
- `start_suite.sh`：一键启动脚本。
- `stop_suite.sh`：一键停止脚本。
- `web_api.py` / `web_ui_html.py`：Web API 和前端页面。

## 2. 固定端口

本交付版本使用以下端口：

| 服务 | 端口 | 说明 |
|---|---:|---|
| server_agent 控制器 socket | `6001` | Ryu 控制器连接根控 |
| Web UI | `6009` | 浏览器访问拓扑和流表页面 |
| DRL path_service | `8889` | server_agent 长连接调用 DRL 路径计算 |
| Ryu 控制器 c1 | `6654` | Military domain1 |
| Ryu 控制器 c2 | `6655` | Military domain2 |
| Ryu 控制器 c3 | `6656` | Military domain3 |
| Ryu 控制器 c4 | `6657` | Military domain4 |
| Ryu 控制器 c5 | `6658` | Military domain5 |
| Ryu 控制器 c6 | `6659` | Military domain6 |
| Ryu 控制器 c7 | `6670` | Military domain7 |

## 3. 环境准备

建议在 Linux 或 Mininet 虚拟机中运行。Windows 下可以编辑代码，但 Mininet/Ryu 联调通常需要 Linux 环境。

需要准备：

- Python 3
- Mininet
- Open vSwitch
- Ryu
- Flask
- flask-cors
- networkx
- DRL-OR-S 运行依赖

在交付目录中安装 Python 依赖：

```bash
cd drl-or-s-routing-suite
pip3 install -r requirements.txt
```

如果当前环境已安装上述依赖，可以跳过。

说明：

- `requirements.txt` 只包含本交付包运行需要的 Python 依赖。
- Mininet 和 Open vSwitch 建议通过系统包安装，例如 Ubuntu/Mininet 虚拟机中的 `apt`，不要只依赖 `pip`。
- PyTorch Geometric 在部分环境下需要匹配 Python、PyTorch 和 CUDA/CPU 版本安装；如果 `pip3 install -r requirements.txt` 安装失败，优先按当前机器的 PyTorch 版本安装对应的 PyTorch Geometric wheel。

## 4. 启动方式一：一键启动

进入交付目录：

```bash
cd drl-or-s-routing-suite
```

启动：

```bash
./start_suite.sh
```

如果要打通 Mininet 虚拟交换机和真实 SDN 交换机，把连接真实交换机的数据面物理网卡作为参数传入，例如：

```bash
./start_suite.sh eno1
```

该模式会：

- 让真实交换机仍连接 `c1` 控制器端口 `6654`。
- 自动设置 `EXTERNAL_LINK_PORTS=1:20`，让控制器从启动时就把 `s1:port20` 当作外部/链路端口，避免误学主机。
- 将宿主机物理网卡 `eno1` 加入 Mininet OVS `s1`，并固定 OpenFlow 端口为 `20`。
- 退出 Mininet CLI 后恢复该物理网卡原 IP 和默认网关。

该脚本会依次启动：

1. DRL path_service：`127.0.0.1:8889`
2. server_agent：socket `6001`，Web `6009`
3. 7 个 Ryu 控制器：`6654/6655/6656/6657/6658/6659/6670`
4. Military Mininet 拓扑：`sudo python3 testbed/creat_test_topo.py`

一键脚本最后会停留在当前终端的 Mininet CLI 中。你如果在 PyCharm 的 Terminal 面板里运行 `./start_suite.sh`，Mininet CLI 就会直接显示在 PyCharm 终端中；脚本不会主动打开 Ubuntu 图形终端。退出 Mininet CLI 后，脚本会自动清理后台服务。

启动后访问 Web：

```text
http://localhost:6009
```

停止：

```bash
./stop_suite.sh
```

日志位置：

```text
drl-or-s-routing-suite/logs/
```

常看日志：

```bash
tail -f logs/path_service.log
tail -f logs/server_agent.log
tail -f logs/server_agent.stdout.log
tail -f logs/controllers.log
tail -f logs/ryu_controller_<PORT>.log
tail -f logs/mininet_topology.log
```

`logs/server_agent.log` 是 `server_agent.py` 自身 logging 模块写入的主日志；`logs/server_agent.stdout.log` 只保存后台启动时的 stdout/stderr 输出。

## 5. 启动方式二：手动分步启动

如果一键启动失败，建议按下面顺序手动启动，方便定位问题。

### 5.1 启动 DRL path_service

```bash
cd drl-or-s-routing-suite
python3 drl-or-s/path_service.py --topo Military --port 8889
```

期望现象：

- 控制台显示 DRL 路径计算服务启动。
- 服务监听 `8889`。
- 如果 DRL 模型不可用，后续 server_agent 仍应能够回退 Dijkstra。

### 5.2 启动 server_agent

新开一个终端：

```bash
cd drl-or-s-routing-suite
python3 server_agent.py hybrid
```

期望现象：

- server_agent 监听控制器 socket `6001`。
- Flask Web 监听 `6009`。
- 日志中能看到 Web 服务器启动信息。
- 若 DRL path_service 已启动，日志中应出现已连接 path_service 的信息。

### 5.3 启动 Ryu 控制器

新开一个终端：

```bash
cd drl-or-s-routing-suite
python3 start_controllers_test.py start -n
```

期望现象：

- 7 个 Ryu 控制器后台启动。
- 控制器端口分别为 `6654/6655/6656/6657/6658/6659/6670`。
- server_agent 日志中逐步出现控制器连接和心跳。

停止控制器：

```bash
python3 start_controllers_test.py stop
```

如果确实想让每个 Ryu 控制器弹出独立 Ubuntu 图形终端，可手动使用：

```bash
python3 start_controllers_test.py start --terminal
```

日常调试更建议使用默认后台模式，启动器日志写入 `logs/controllers.log`，每个 Ryu 控制器的运行日志写入 `logs/ryu_controller_<PORT>.log`，这样在 PyCharm Terminal 中更稳定。

## 6. 启动 Military 拓扑

确认 DRL、server_agent、Ryu 控制器都已启动后，启动 Mininet 拓扑：

```bash
cd drl-or-s-routing-suite
sudo python3 testbed/creat_test_topo.py
```

如果要接入真实交换机，先确认真实交换机控制通道连接到 `6654`，再把连接真实交换机的数据面网卡传给脚本：

```bash
cd drl-or-s-routing-suite
sudo EXTERNAL_LINK_PORTS=1:20 python3 testbed/creat_test_topo.py eno1
```

启动后重点检查：

```bash
sh ovs-vsctl get Interface eno1 ofport
sh ovs-vsctl show
```

期望 `eno1` 的 ofport 为 `20`，并且 `s1` 仍连接 `tcp:127.0.0.1:6654` 或你的控制器实际地址。

如果脚本异常退出导致宿主机网卡未恢复，可手动执行：

```bash
sudo ovs-vsctl --if-exists del-port s1 eno1
sudo ip link set eno1 up
sudo ip addr add <原IP/掩码> dev eno1
sudo ip route replace default via <原网关> dev eno1
```

该拓扑会创建：

- 7 个域
- 47 个交换机
- Military 验收拓扑中的跨域链路
- 部分交换机挂载主机

进入 Mininet CLI 后，可执行：

```bash
net
nodes
links
dump
```

检查交换机是否连接控制器：

```bash
sh ovs-vsctl show
```

## 7. Web/API 冒烟测试

在 server_agent 启动后，执行：

```bash
curl http://localhost:6009/api/health
curl http://localhost:6009/api/statistics
curl http://localhost:6009/api/controllers
curl http://localhost:6009/api/graph
curl http://localhost:6009/api/route_sessions
```

期望结果：

- 都返回 JSON。
- `/api/health` 表示服务可用。
- `/api/statistics` 中能看到 controller、switch、host、link、DRL 状态。
- `/api/graph` 中节点和边数量随拓扑上报增加。
- 虚实通信模式下，真实交换机连接 `c1:6654` 并被 LLDP 发现后，会作为交换机节点和链路同步到 `/api/graph`。
- `/api/route_sessions` 初始可以为空，产生路径后应逐步出现记录。

浏览器检查：

```text
http://localhost:6009
```

重点确认：

- Web 页面能打开。
- 拓扑图能显示。
- 控制器、交换机、主机数量正常。
- route sessions 面板存在。
- 手动流表新增/删除入口存在。

## 8. 跨域连通性测试

在 Mininet CLI 中执行分批 ping：

```bash
py net.staggered_pingall(interval=0.3, bidirectional=False, count=1, timeout=1)
```

如果需要双向测试：

```bash
py net.staggered_pingall(interval=0.3, bidirectional=True, count=1, timeout=1)
```

期望结果：

- 输出 `total/success/failed/loss`。
- 跨域 ping 能逐步成功。
- 首次跨域通信时允许有短暂建路延迟，但不应出现大面积持续丢包。

也可以指定主机单独测试，例如：

```bash
h7 ping -c 3 h14
h7 ping -c 3 h21
h28 ping -c 3 h35
```

主机名以实际 `nodes` 输出为准。

## 9. DRL 路径计算测试

### 9.1 DRL 可用测试

确保 `path_service.py` 正常运行后，在 Mininet 中触发跨域流量：

```bash
h7 ping -c 3 h35
```

观察 server_agent 日志：

```bash
tail -f logs/server_agent.log
```

期望现象：

- server_agent 调用 DRL path_service。
- path response 中包含完整路径。
- 控制器收到 path response 后安装流表。

观察 path_service 日志：

```bash
tail -f logs/path_service.log
```

期望现象：

- 能看到 `path_request`。
- 能看到路径计算结果。

### 9.2 DRL fallback 测试

停止 path_service：

```bash
kill $(cat logs/path_service.pid)
rm -f logs/path_service.pid
```

继续触发跨域 ping：

```bash
h7 ping -c 3 h35
```

期望现象：

- server_agent 日志提示 DRL 不可用或调用失败。
- server_agent 使用 Dijkstra fallback。
- 路径仍能计算并下发。

恢复 path_service：

```bash
python3 drl-or-s/path_service.py --topo Military --port 8889 > logs/path_service.log 2>&1 &
echo $! > logs/path_service.pid
```

### 9.3 DRL 路由模式

可通过环境变量控制 DRL 使用方式：

- `DRL_ROUTE_MODE=shadow`：默认推荐。DRL 旁路给出建议，实际仍下发 fallback 路径。
- `DRL_ROUTE_MODE=hybrid`：DRL 路径通过校验后下发，失败回退 Dijkstra。
- `DRL_ROUTE_MODE=spf`：只使用 Dijkstra/最短路径。
- `DRL_ROUTE_MODE=drl`：强制 DRL，仅用于实验。

也可以在手动启动 `server_agent.py` 时直接把模式作为位置参数传入：

```bash
python3 server_agent.py shadow
python3 server_agent.py hybrid
python3 server_agent.py spf
python3 server_agent.py drl
```

命令行参数会覆盖 `DRL_ROUTE_MODE` 环境变量；不传参数时继续使用环境变量或默认 `shadow`。

建议上线顺序：先 `shadow`，确认 `model_used=true` 的路径稳定后，再切换到 `hybrid`。

## 10. 手动流表测试

### 10.1 通过 Web 页面测试

打开：

```text
http://localhost:6009
```

操作步骤：

1. 点击某个交换机。
2. 打开流表面板。
3. 新增一条手动流表。
4. 查看交换机流表展示是否更新。
5. 删除刚才新增的手动流表。
6. 确认 Web 状态同步更新。

### 10.2 通过 API 测试

新增手动流表示例：

```bash
curl -X POST http://localhost:6009/api/flows \
  -H "Content-Type: application/json" \
  -d '{
    "switch_id": 7,
    "out_port": 1,
    "priority": 10,
    "match": {
      "eth_type": 2048,
      "ipv4_dst": "10.0.0.35"
    }
  }'
```

删除手动流表示例：

```bash
curl -X DELETE http://localhost:6009/api/flows \
  -H "Content-Type: application/json" \
  -d '{
    "switch_id": 7,
    "flow_id": 123456789
  }'
```

注意：删除时的 `flow_id` 要使用新增接口返回的真实 ID。

## 11. route sessions 测试

先触发跨域流量：

```bash
h7 ping -c 3 h35
h14 ping -c 3 h28
```

然后查询：

```bash
curl http://localhost:6009/api/route_sessions
```

期望结果：

- 返回 JSON。
- `sessions` 中出现路径会话。
- Web 页面中 route sessions 面板能看到路径。
- 点击路径会话后，页面应能高亮或展示对应路径。

## 12. 常见问题排查

### 12.1 Web 打不开

检查 server_agent 是否启动：

```bash
ps aux | grep server_agent.py
curl http://localhost:6009/api/health
```

检查端口：

```bash
ss -lntp | grep 6009
```

### 12.2 控制器没有连接 server_agent

检查 server socket：

```bash
ss -lntp | grep 6001
```

检查控制器日志：

```bash
tail -f logs/controllers.log
tail -f logs/ryu_controller_<PORT>.log
```

确认 `common_config.py` 中端口为 `6001`。

### 12.3 DRL 服务不可用

检查端口：

```bash
ss -lntp | grep 8889
```

检查日志：

```bash
tail -f logs/path_service.log
tail -f logs/server_agent.log
```

如果 DRL 失败但 Dijkstra fallback 正常，跨域路径仍应可用。

### 12.4 Mininet 旧状态影响测试

清理 Mininet：

```bash
sudo mn -c
```

再重新启动：

```bash
sudo python3 testbed/creat_test_topo.py
```

### 12.5 端口被占用

查看占用：

```bash
ss -lntp | grep -E "6001|6009|8889|6654|6655|6656|6657|6658|6659|6670"
```

停止旧进程：

```bash
./stop_suite.sh
```

必要时手动结束残留进程。

## 13. 完整验收流程

建议按以下顺序验收：

1. 清理旧 Mininet 状态：

   ```bash
   sudo mn -c
   ```

2. 启动交付项目并进入 Military Mininet CLI：

   ```bash
   cd drl-or-s-routing-suite
   ./start_suite.sh
   ```

3. 检查 Web/API：

   ```bash
   curl http://localhost:6009/api/health
   curl http://localhost:6009/api/statistics
   ```

4. 在 Mininet CLI 中测试：

   ```bash
   py net.staggered_pingall(interval=0.3, bidirectional=False, count=1, timeout=1)
   ```

5. 检查 route sessions：

   ```bash
   curl http://localhost:6009/api/route_sessions
   ```

6. 在 Web 页面检查拓扑、路径会话、手动流表。

7. 停止项目：

   ```bash
   ./stop_suite.sh
   sudo mn -c
   ```

## 14. 验收通过标准

满足以下条件即可认为基础验收通过：

- `start_suite.sh` 能启动 DRL、server_agent、7 个 Ryu 控制器，并进入 Military Mininet CLI。
- Web UI 可以通过 `http://localhost:6009` 打开。
- `/api/health`、`/api/statistics`、`/api/graph` 返回 JSON。
- Military 拓扑可以启动并进入 Mininet CLI。
- 跨域 ping 能成功建立路径。
- route sessions 能在 API 和 Web 中展示。
- 手动流表新增/删除功能可用。
- DRL 不可用时，server_agent 能回退 Dijkstra。
