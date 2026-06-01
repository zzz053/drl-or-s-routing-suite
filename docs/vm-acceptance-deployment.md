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
- 控制面端口、Web API、拓扑一致性、Mininet 主机路由、ping 和关键流表。

这意味着如果配置写成 `eno1`，但运行拓扑实际挂的是 `ens34`，`health` 会失败，而不会再把配置漂移误判为通过。

## 6. 生成报告

```bash
SUDO_PASSWORD=h PYTHON_BIN=python3 ./acceptance.sh report
```

报告会生成到：

```text
reports/acceptance-report-YYYYMMDD-HHMMSS.md
```

## 7. 停止环境

```bash
SUDO_PASSWORD=h PYTHON_BIN=python3 ./acceptance.sh stop
```

脚本会停止项目后台进程并执行 `sudo mn -c` 清理 Mininet/OVS 残留。

## 8. 常见问题

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
