#!/usr/bin/env python3
"""
基于 creat_test_topo.py 裁剪的三域拓扑：
- Domain1（控制器 6654）
- Domain3（控制器 6655）
- Domain4（控制器 6656）

可与 start_controllers_simple.py 配合使用：
  python3 start_controllers_simple.py start
  sudo python3 create_domain134_topo.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info


# 与 start_controllers_simple.py 对齐：仅 3 个从控制器
DOMAIN_CONTROLLER_PORTS = {
    "domain1": 6654,
    "domain3": 6655,
    "domain4": 6656,
}


def build_domain134_topology():
    net = Mininet(controller=None, switch=OVSSwitch)

    # ===== 域定义（沿用 creat_test_topo.py）=====
    domain1_switches = [1, 2, 3, 4, 5, 6, 45, 46, 47]
    domain3_switches = [28, 31, 30, 29, 34, 33, 32]
    # 域4是 root=7 的 7 节点树
    domain4_switches = [7, 8, 9, 10, 11, 12, 13]

    # 域内链路
    domain1_intra_links = [
        (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1),
        (46, 3), (46, 4), (46, 5), (46, 47),
        (45, 1), (45, 2), (45, 3), (45, 47),
        (47, 1), (47, 6),
    ]
    domain3_intra_links = [
        (28, 31), (28, 30), (28, 29),
        (31, 34), (30, 33), (29, 32),
    ]
    domain4_intra_links = [
        (7, 8), (7, 9), (7, 10),
        (8, 11), (9, 12), (10, 13),
    ]

    # 域间链路（沿用原始中这三个域相关的连接）
    inter_domain_links = [
        (28, 1), (28, 6),  # domain3 <-> domain1
        (7, 3), (7, 4),    # domain4 <-> domain1
    ]

    # ===== 控制器 =====
    info("*** 添加 3 个从控制器（6654-6656）\n")
    c1 = net.addController("c1", controller=RemoteController, ip="127.0.0.1", port=6654)
    c2 = net.addController("c2", controller=RemoteController, ip="127.0.0.1", port=6655)
    c3 = net.addController("c3", controller=RemoteController, ip="127.0.0.1", port=6656)
    controllers = {"domain1": c1, "domain3": c2, "domain4": c3}

    # ===== 交换机 =====
    info("*** 添加交换机\n")
    switches = {}
    for sid in domain1_switches + domain3_switches + domain4_switches:
        switches[sid] = net.addSwitch(f"s{sid}")

    # ===== 主机 =====
    # 与原脚本风格一致：Domain1 不挂主机；Domain3 / Domain4 每台交换机挂 1 台主机
    info("*** 添加主机（Domain1 不挂主机，Domain3/4 每交换机 1 台）\n")
    host_index = 1
    for sid in domain3_switches + domain4_switches:
        host_name = f"h{sid}"
        host_ip = f"10.0.0.{host_index}/8"
        host = net.addHost(host_name, ip=host_ip)
        net.addLink(host, switches[sid])
        host_index += 1

    # ===== 链路 =====
    info("*** 添加域内链路\n")
    for u, v in domain1_intra_links + domain3_intra_links + domain4_intra_links:
        net.addLink(switches[u], switches[v])

    info("*** 添加域间链路\n")
    for u, v in inter_domain_links:
        net.addLink(switches[u], switches[v])

    # ===== 启动网络 =====
    info("*** 启动网络\n")
    net.start()

    # 按域绑定控制器
    info("*** 绑定交换机到对应域控制器\n")
    domain_map = {
        "domain1": domain1_switches,
        "domain3": domain3_switches,
        "domain4": domain4_switches,
    }
    for domain_name, sw_ids in domain_map.items():
        ctrl = controllers[domain_name]
        for sid in sw_ids:
            switches[sid].start([ctrl])

    # 再显式设置 OVS 控制器，确保与 simple 控制器端口严格一致
    info("*** 显式设置每台交换机控制器地址\n")
    for sid in domain1_switches:
        switches[sid].cmd(f"ovs-vsctl set-controller s{sid} tcp:127.0.0.1:{DOMAIN_CONTROLLER_PORTS['domain1']}")
    for sid in domain3_switches:
        switches[sid].cmd(f"ovs-vsctl set-controller s{sid} tcp:127.0.0.1:{DOMAIN_CONTROLLER_PORTS['domain3']}")
    for sid in domain4_switches:
        switches[sid].cmd(f"ovs-vsctl set-controller s{sid} tcp:127.0.0.1:{DOMAIN_CONTROLLER_PORTS['domain4']}")

    info("*** 三域拓扑已启动：Domain1(6654), Domain3(6655), Domain4(6656)\n")
    info("*** 进入 Mininet CLI\n")
    CLI(net)
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    build_domain134_topology()
