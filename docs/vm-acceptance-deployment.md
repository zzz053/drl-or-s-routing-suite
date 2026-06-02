# VM 验收部署说明

本文档用于把当前项目拷贝到 Linux 虚拟机后，完成 Mininet 虚拟网络与真实 SDN 交换机网络的虚实通信验收。

## 1. 配置文件选择

项目现在保留三类验收配置：

- `config/hybrid_acceptance.json`：当前默认验收配置。当前 VM 环境使用 `ens34`。
- `config/hybrid_acceptance.vm.json`：VM 环境模板，`external_interface` 固定为 `ens34`。
- `config/hybrid_acceptance.server.json`：服务器环境模板，`external_interface` 暂保留为 `eno1`，必须在服务器实机可访问后用 `ip link` 和 OVS 结果复核。

如果换到另一台 VM 或服务器，不要沿用旧网口名。先执行：

```bash
ip addr
ip route show default
```

管理网卡通常承载 SSH 和默认路由，不能直接加入 OVS。外部数据面网卡应连接真实 SDN 网络，且不承载默认路由。

## 2. VM 当前网口约定

当前已验证的 VM 网口是：

- `ens33`：管理网卡，承载默认路由和 SSH。
- `ens34`：外部数据面网卡，接入真实交换机侧网络。

因此 VM 上应使用：

```json
{
  "external_interface": "ens34"
}
```

不要在 VM 上使用服务器侧的 `eno1` 配置。`eno1` 只适用于服务器实机网口名确认为 `eno1` 的环境。

## 3. 静态虚实边界

当前 P0 验收使用静态虚实边界，不依赖 VM 边界上的 LLDP 自动发现。

默认边界约定：

- VM 外部数据面网卡加入 Mininet 边界交换机 `s1`。
- OpenFlow 端口固定为 `s1:20`。
- 真实主机默认验收目标为 `192.168.103.3`。

对应配置：

```json
"hybrid": {
  "external_link_ports": [
    {"dpid": 1, "port": 20}
  ],
  "static_links": [
    {
      "src_dpid": 1,
      "src_port": 20,
      "dst_dpid": 128986965761,
      "dst_port": 11,
      "delay_ms": 0,
      "bandwidth_mbps": 800,
      "loss_percent": 0
    }
  ],
  "gateway_ip": "10.0.0.254",
  "gateway_mac": "02:00:00:00:fe:01",
  "real_routes": ["192.168.103.0/24"]
}
```

## 4. 启动验收环境

进入项目目录：

```bash
cd /home/hydrate/a/drl-or-s-routing-suite
```

启动：

```bash
SUDO_PASSWORD=h PYTHON_BIN=python3 MININET_PYTHON=/usr/bin/python3 ./acceptance.sh start
```

脚本会读取 `config/hybrid_acceptance.json`，再启动：

- `drl-or-s/path_service.py`
- `server_agent.py hybrid`
- Ryu 控制器端口 `6654,6655,6656,6657,6658,6659,6670`
- Mininet 拓扑，并把 `external_interface` 加入边界 OVS

## 5. 健康检查

运行：

```bash
SUDO_PASSWORD=h PYTHON_BIN=python3 ./acceptance.sh health
```

健康检查现在会验证配置与运行时状态是否一致，包括：

- `external_interface` 是否存在。
- `external_interface` 是否没有承载默认路由。
- 该网口是否实际加入配置中的 OVS 边界交换机，例如 `s1`。
- OVS 中该网口的 `ofport` 是否等于配置中的边界端口，例如 `20`。
- `server_agent.py` 进程中的关键环境变量是否与 JSON 导出的运行配置一致。
- 控制面端口、Web API、拓扑一致性、Mininet 主机路由、ping 和关键流表。
- `h28 -> 真实主机` 的主动 ping RTT、丢包率和估算单向延迟。

这意味着如果配置写成 `eno1`，但运行拓扑实际挂的是 `ens34`，`health` 会失败，而不会再把配置漂移误判为通过。

## 6. JSON 运行配置范围

`config/hybrid_acceptance.json` 会在 `acceptance.sh start` 时导出为运行环境变量。当前 JSON 会驱动：

- `controllers.ports`：控制器启动端口和 health 端口检查。
- `runtime.route_mode`：`server_agent.py` 路由模式，同时导出 `DRL_ROUTE_MODE`。
- `runtime.drl_*`：DRL 候选路径数量、推理超时和最小置信度。
- `runtime.route_flow_*`：自动路径流表 idle/hard timeout。
- `hybrid.external_switch` / `hybrid.external_port`：Mininet 外部网卡加入哪个 OVS 交换机和 ofport。
- `hybrid.external_link_ports`：控制器外部边界端口白名单和 health 校验。
- `hybrid.external_link_metrics`：外部边界端口的配置型 delay/bandwidth/loss，控制器用于边界链路权重。
- `hybrid.static_links`：显式注入无法依赖 LLDP 发现的虚实 OpenFlow 交换机链路，例如 `s1:20 <-> 128986965761:11`。
- `load_test.*`：随机打流测试默认参数。

命令行环境里的同名变量会被 JSON 导出的值覆盖。敏感项和本机路径仍不放入 JSON，例如 `SUDO_PASSWORD`、`PYTHON_BIN`、`MININET_PYTHON`、`PATH_SERVICE_PYTHON`。

## 7. 虚实延迟测量说明

虚实边界不再依赖 LLDP 作为验收前提。当前有两类延迟来源：

- 运行权重：`hybrid.external_link_metrics` 给控制器提供边界端口的配置型 metric。现场未知时可保留 `delay_ms=0`，避免伪造精确数据。
- 实测证据：`./acceptance.sh health` 会从 Mininet 验证主机主动 ping 真实主机，解析 `min/avg/max/mdev` RTT 和丢包率，并输出 `virtual_real_latency`。

`virtual_real_latency` 是端到端测量，包含虚拟主机、Mininet、OVS、VM 外部网卡、真实交换机和真实主机路径，不等同于单条物理链路的精确单向时延。报告中的 `estimated_one_way_ms` 只是用 RTT/2 给出的工程估计。

## 8. 生成报告

```bash
SUDO_PASSWORD=h PYTHON_BIN=python3 ./acceptance.sh report
```

报告会生成到：

```text
reports/acceptance-report-YYYYMMDD-HHMMSS.md
```

## 9. 停止环境

```bash
SUDO_PASSWORD=h PYTHON_BIN=python3 ./acceptance.sh stop
```

脚本会停止项目后台进程并执行 `sudo mn -c` 清理 Mininet/OVS 残留。

## 10. 常见问题

### 网口名不对

现象：

- `./acceptance.sh start` 提示外部网卡不存在。
- `./acceptance.sh health` 提示外部网卡未加入预期 OVS 边界。

处理：

```bash
ip link
sudo ovs-vsctl show
sudo ovs-vsctl port-to-br <EXTERNAL_INTERFACE>
sudo ovs-vsctl get Interface <EXTERNAL_INTERFACE> ofport
```

把实际数据面网卡写入 `config/hybrid_acceptance.json` 的 `external_interface`。

### 网口承载默认路由

如果 `external_interface` 是默认路由出口，脚本会拒绝启动，因为把管理网卡加入 OVS 可能中断 SSH。

只有在现场确认这是有意设计时，才允许：

```bash
export ALLOW_EXTERNAL_INTF_HAS_DEFAULT_ROUTE=1
```

### VM 边界 LLDP 不工作

VM 或宿主机可能拦截 LLDP，导致 Mininet 无法自动发现真实交换机边界链路。当前 P0 验收不依赖该能力，使用静态虚实边界：

```json
"external_link_ports": [
  {"dpid": 1, "port": 20}
]
```

### 第一次 ping 失败但第二次通过

第一次 ping 可能只触发 ARP、路径计算和流表下发。验收以 warmup 后的 verification ping 为准。
