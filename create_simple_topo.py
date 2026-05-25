#!/usr/bin/env python
"""
Mininet 简单拓扑脚本（与 create_complex_topo.py 约定一致，二者不同时运行）
- 三个域，交换机数量 1 + 2 + 1 = 4 台；在链状骨干上增加 s2-s4 链路，形成环路（s2-s3-s4-s2）
- 根控：手动启动 server_agent.py（TCP 5001）；从控 Ryu 由 start_controllers_simple.py 启动（6654-6656）
- 从控通过 controller.py 内 TCP 连接根控（默认 127.0.0.1:5001，见 SERVER_AGENT_IP/PORT）
- 主机 IP：10.0.1.x、10.0.2.x、10.0.3.x，掩码 /8，无默认网关（与复杂拓扑相同）

推荐顺序: server_agent.py -> start_controllers_simple.py start -> sudo python3 create_simple_topo.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink

# 各交换机对应的 OpenFlow 从控端口（与 start_controllers_simple 一致）
SWITCH_CONTROLLER_MAP = {
    's1': 6654,
    's2': 6655,
    's3': 6655,
    's4': 6656,
}


class SimpleTopo:
    """
    简单三域拓扑（4 交换机）
    骨干：s1 -- s2 -- s3 -- s4；另增 s2 -- s4，与 s2-s3-s4 构成环路。
    """

    def __init__(self):
        self.net = None

    def build(self):
        info('*** 创建 Mininet 简单网络\n')

        self.net = Mininet(controller=RemoteController, link=TCLink)

        info('*** 添加控制器（从控 6654-6656；根控为 server_agent.py，交换机不直连根控 OpenFlow）\n')
        controllers = []
        for i in range(3):
            c = self.net.addController(
                name='c%d' % (i + 1),
                controller=RemoteController,
                ip='127.0.0.1',
                port=6654 + i,
            )
            controllers.append(c)
            info('从控制器 c%d: 127.0.0.1:%d\n' % (i + 1, 6654 + i))

        info('*** 添加交换机\n')
        s1 = self.net.addSwitch('s1', dpid='0000000000000001')
        s2 = self.net.addSwitch('s2', dpid='0000000000000002')
        s3 = self.net.addSwitch('s3', dpid='0000000000000003')
        s4 = self.net.addSwitch('s4', dpid='0000000000000004')

        info('*** 添加交换机链路（含环路：s2-s3-s4-s2）\n')
        # Domain1 -- Domain2 边界
        self.net.addLink(s1, s2, port1=1, port2=1)
        # Domain2 内部
        self.net.addLink(s2, s3, port1=2, port2=1)
        # Domain2 -- Domain3 边界
        self.net.addLink(s3, s4, port1=2, port2=1)
        # 环路：s2 <-> s4（s2 已用 1-3，s4 已用 1-2）
        self.net.addLink(s2, s4, port1=4, port2=3)

        info('*** 添加主机（每交换机 1～2 台，/8，无网关）\n')
        # Domain 1：s1 上 2 台
        h1 = self.net.addHost('h1', ip='10.0.1.1/8', mac='00:00:00:00:00:01')
        h2 = self.net.addHost('h2', ip='10.0.1.2/8', mac='00:00:00:00:00:02')
        self.net.addLink(s1, h1, port1=2, port2=1)
        self.net.addLink(s1, h2, port1=3, port2=1)

        # Domain 2：s2 上 1 台，s3 上 2 台
        h3 = self.net.addHost('h3', ip='10.0.2.1/8', mac='00:00:00:00:00:03')
        self.net.addLink(s2, h3, port1=3, port2=1)

        h4 = self.net.addHost('h4', ip='10.0.2.2/8', mac='00:00:00:00:00:04')
        h5 = self.net.addHost('h5', ip='10.0.2.3/8', mac='00:00:00:00:00:05')
        self.net.addLink(s3, h4, port1=3, port2=1)
        self.net.addLink(s3, h5, port1=4, port2=1)

        # Domain 3：s4 上 1 台
        h6 = self.net.addHost('h6', ip='10.0.3.1/8', mac='00:00:00:00:00:06')
        self.net.addLink(s4, h6, port1=2, port2=1)

        # 不在此处调用 switch.start() / ovs-vsctl：Mininet 会在 net.start() 里
        # 统一拉起交换机；若提前 start，会与 net.start() 内再次 start（且带全部
        # RemoteController）冲突，导致网桥被删建两次、控制器列表错乱。
        # 正确的 per-switch 控制器在 main() 里 net.start() 之后用 set-controller 设置。

        info('*** 简单拓扑说明\n')
        info('  Domain1(6654): s1 — h1,h2 (10.0.1.1-2/8)\n')
        info('  Domain2(6655): s2—s3 — h3(10.0.2.1), h4,h5(10.0.2.2-3)\n')
        info('  Domain3(6656): s4 — h6 (10.0.3.1/8)\n')
        info('  交换机: s1-s2-s3-s4 链 + s2-s4（存在环路，需控制器/STP 等策略避免二层风暴）\n')

        return self.net


def main():
    setLogLevel('info')
    topo = SimpleTopo()
    net = topo.build()

    info('*** 启动网络（配置主机，拉起交换机后再绑定各从控端口）\n')
    net.start()

    info('*** 为每台交换机指定唯一 OpenFlow 从控（覆盖 net.start 时的多控制器占位）\n')
    for switch_name, expected_port in sorted(SWITCH_CONTROLLER_MAP.items()):
        switch = net.get(switch_name)
        switch.cmd('ovs-vsctl set-controller %s tcp:127.0.0.1:%d' % (
            switch_name, expected_port))
        controller_info = switch.cmd('ovs-vsctl get-controller %s' % switch_name).strip()
        info('  %s -> %s (期望 tcp:127.0.0.1:%d)\n' % (
            switch_name, controller_info, expected_port))

    info('*** Mininet CLI（可先启动: python3 start_controllers_simple.py）\n')
    info('  pingall / h1 ping -c2 10.0.3.1 等\n')
    CLI(net)

    info('*** 停止网络\n')
    net.stop()


if __name__ == '__main__':
    main()
