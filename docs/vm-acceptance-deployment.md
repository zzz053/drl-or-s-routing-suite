# VM 验收部署说明

本文档用于把当前项目拷贝到验收单位的 Linux 虚拟机后，完成 Mininet 虚拟网络与真实 SDN 交换机网络的虚实通信验收。

## 1. VM 拷贝和首次启动

1. 将完整项目目录拷贝到 Linux VM。
2. 首次启动后进入项目根目录。
3. 确认项目目录中存在：
   - `acceptance.sh`
   - `config/hybrid_acceptance.json`
   - `server_agent.py`
   - `drl-or-s/path_service.py`
   - `testbed/creat_test_topo.py`

## 2. 必需 Linux 包和 Python 环境

验收 VM 需要具备现有项目运行环境：

- Python 3
- Ryu
- Mininet
- Open vSwitch
- Flask
- NetworkX
- PyTorch 及项目 DRL 依赖

如果服务器上使用 conda 环境，建议继续使用原环境；脚本会优先识别：

```bash
$HOME/miniconda3/envs/ryu_drl_s/bin/python
```

也可以显式指定：

```bash
PYTHON_BIN=python3 PATH_SERVICE_PYTHON=/path/to/python ./acceptance.sh start
```

## 3. 网卡模式和接口命名

VM 必须有一块可以接入真实交换机网络的网卡。建议使用桥接或直通方式，让 Linux VM 内部能看到该网卡。

查看网卡名：

```bash
ip link
```

把实际网卡名写入：

```text
config/hybrid_acceptance.json
```

示例：

```json
{
  "external_interface": "eno1"
}
```

## 4. 真实交换机接线方式

当前 P0 方案使用静态虚实边界，不依赖 VM 边界上的 LLDP 自动发现。

推荐接线：

- Linux VM 外部网卡连接真实交换机网络。
- Mininet 内部将该网卡接入 `s1`。
- OpenFlow 端口固定为 `s1:20`。
- 真实主机 `192.168.103.3` 位于真实交换机侧。

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

## 5. 静态虚实边界配置

编辑：

```bash
vim config/hybrid_acceptance.json
```

重点字段：

- `external_interface`：VM 内真实接线网卡名。
- `hybrid.external_link_ports`：真实网络接入 Mininet 的静态边界端口。
- `hybrid.gateway_ip`：虚拟主机访问真实网段时使用的网关 IP。
- `hybrid.real_routes`：真实主机所在网段。
- `validation.virtual_host_name`：验收源主机，默认 `h28`。
- `validation.virtual_host_ip`：验收源 IP，默认 `10.0.0.28`。
- `validation.real_host_ip`：真实侧主机 IP，当前应为 `192.168.103.3`。

## 6. 启动系统

在项目根目录运行：

```bash
./acceptance.sh start
```

脚本会启动：

- DRL `path_service`
- `server_agent.py hybrid`
- 7 个 Ryu 控制器，端口为 `6654,6655,6656,6657,6658,6659,6670`
- Mininet 军事拓扑

Mininet/OVS 操作需要 sudo，脚本会在拓扑启动时使用：

```bash
sudo -E python3 -u testbed/creat_test_topo.py "$EXTERNAL_INTF"
```

## 7. 健康检查

运行：

```bash
./acceptance.sh health
```

检查内容：

- `server_agent` 端口 `6001`
- Web 端口 `6009`
- `path_service` 端口 `8889`
- Ryu 端口 `6654,6655,6656,6657,6658,6659,6670`
- 禁用端口 `6671` 不应监听
- Web API 状态
- 最近严重日志
- `h28 -> 192.168.103.3` warmup 和 verification ping
- `s28/s1` 双向虚实流表

输出结论为：

- `通过`
- `有风险`
- `失败`

## 8. 生成验收报告

运行：

```bash
./acceptance.sh report
```

报告会生成到：

```text
reports/acceptance-report-YYYYMMDD-HHMMSS.md
```

报告包含配置摘要、端口状态、控制器状态、Web API 状态、虚实边界状态、数据面验证、流表摘要和最终结论。

## 9. 停止系统

运行：

```bash
./acceptance.sh stop
```

脚本会停止控制器和后台进程，并执行：

```bash
sudo mn -c
```

该命令用于清理 Mininet/OVS 残留状态。

## 10. 常见故障处理

### 网卡名错误

现象：`./acceptance.sh start` 提示外部网卡不存在。

处理：

```bash
ip link
```

将正确网卡名写入 `config/hybrid_acceptance.json` 的 `external_interface`。

### sudo 权限缺失

现象：Mininet 或 OVS 操作失败。

处理：确认当前用户可执行：

```bash
sudo mn -c
```

### `6671` 异常监听

现象：健康检查报告禁用端口 `6671` 正在监听。

处理：停止旧控制器或旧实验进程，确保真实交换机使用的控制器监听端口恢复到 `6654`。

### Ryu 端口缺失

现象：`6654` 到 `6670` 中有端口未监听。

处理：

```bash
python3 start_controllers_test.py stop
python3 start_controllers_test.py start -n
```

然后重新运行 `./acceptance.sh health`。

### 没有 route session

现象：Web 状态为 `partial`，说明控制面可用但还没有最近路径证据。

处理：从 `h28` 访问真实主机 `192.168.103.3`，触发路径计算后再次检查。

### 第一次 ping 失败但第二次 ping 通过

这是可接受现象。第一次 ping 可能用于触发 ARP、路径计算和流表下发，验收以 warmup 后的 verification ping 为准。

### 跨 VM 边界 LLDP 不工作

VM 或宿主机可能拦截 LLDP，导致 Mininet 无法自动发现真实交换机边界链路。本项目 P0 验收不依赖该能力，采用静态虚实边界配置规避：

```json
"external_link_ports": [
  {"dpid": 1, "port": 20}
]
```
