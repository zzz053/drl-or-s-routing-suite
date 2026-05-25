#!/usr/bin/env python
"""
Mininet网络拓扑创建脚本
创建包含6个域的线性网络拓扑结构（无环路）
每个域有1-2个交换机，交换机之间连接，每个交换机连接多个主机
每个域使用独立的远程控制器（127.0.0.1，端口从6654开始）
采用线性拓扑避免ARP风暴和广播风暴

主机 IP 仍按域区分（10.0.1.x、10.0.2.x …），但统一使用掩码 /8（10.0.0.0/8），
且不设默认网关，使跨域互访走二层直连 ARP，避免原先指向不存在的 10.0.x.254 导致无法 ping 通。
若需真实多子网 + 路由，请改为 /24 并部署路由器或控制器三层转发。
"""

from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink


class ComplexTopo:
    """
    复杂网络拓扑类（线性拓扑）
    包含6个域，每个域有1-2个交换机，交换机连接多个主机
    域间采用线性连接：Domain 1 -> 2 -> 3 -> 4 -> 5 -> 6
    避免环路，防止ARP风暴和广播风暴
    主机地址为 10.0.{域}.x/8，同属 10.0.0.0/8，不设默认路由。
    """
    
    def __init__(self):
        self.net = None
        
    def build(self):
        """构建网络拓扑"""
        info('*** 创建Mininet网络\n')
        
        # 创建网络，使用TCLink以支持带宽和延迟设置
        self.net = Mininet(controller=RemoteController, link=TCLink)
        
        info('*** 添加控制器\n')
        # 为每个域创建独立的远程控制器
        # 端口从6654开始，每个域一个控制器
        controllers = []
        for i in range(6):
            controller = self.net.addController(
                name='c%d' % (i+1),
                controller=RemoteController,
                ip='127.0.0.1',
                port=6654 + i
            )
            controllers.append(controller)
            info('控制器 c%d: 127.0.0.1:%d\n' % (i+1, 6654 + i))
        
        info('*** 添加交换机\n')
        # 创建交换机
        # Domain 1: s1, s2 (2个交换机)
        s1 = self.net.addSwitch('s1', dpid='0000000000000001')
        s2 = self.net.addSwitch('s2', dpid='0000000000000002')
        
        # Domain 2: s3, s4 (2个交换机)
        s3 = self.net.addSwitch('s3', dpid='0000000000000003')
        s4 = self.net.addSwitch('s4', dpid='0000000000000004')
        
        # Domain 3: s5 (1个交换机)
        s5 = self.net.addSwitch('s5', dpid='0000000000000005')
        
        # Domain 4: s6, s7 (2个交换机)
        s6 = self.net.addSwitch('s6', dpid='0000000000000006')
        s7 = self.net.addSwitch('s7', dpid='0000000000000007')
        
        # Domain 5: s8 (1个交换机)
        s8 = self.net.addSwitch('s8', dpid='0000000000000008')
        
        # Domain 6: s9, s10 (2个交换机)
        s9 = self.net.addSwitch('s9', dpid='0000000000000009')
        s10 = self.net.addSwitch('s10', dpid='000000000000000a')
        
        info('*** 添加域内交换机连接\n')
        # Domain 1: s1 <-> s2
        self.net.addLink(s1, s2, port1=1, port2=1)
        
        # Domain 2: s3 <-> s4
        self.net.addLink(s3, s4, port1=1, port2=1)
        
        # Domain 4: s6 <-> s7
        self.net.addLink(s6, s7, port1=1, port2=1)
        
        # Domain 6: s9 <-> s10
        self.net.addLink(s9, s10, port1=1, port2=1)
        
        info('*** 添加域间交换机连接\n')
        # 域间连接：创建跨域链路，形成线性拓扑（避免环路和ARP风暴）
        # Domain 1 <-> Domain 2: s2 <-> s3
        self.net.addLink(s2, s3, port1=4, port2=5)
        
        # Domain 2 <-> Domain 3: s4 <-> s5
        self.net.addLink(s4, s5, port1=4, port2=4)
        
        # Domain 3 <-> Domain 4: s5 <-> s6
        self.net.addLink(s5, s6, port1=5, port2=4)
        
        # Domain 4 <-> Domain 5: s7 <-> s8
        self.net.addLink(s7, s8, port1=5, port2=4)
        
        # Domain 5 <-> Domain 6: s8 <-> s9
        self.net.addLink(s8, s9, port1=5, port2=4)
        
        # 注意：不创建 Domain 6 <-> Domain 1 的连接，避免形成环路
        
        info('*** 添加主机\n')
        # 全网逻辑上同属 10.0.0.0/8：跨 10.0.1.x 与 10.0.2.x 等仍视为同一子网，直连 ARP，无需网关
        # Domain 1: 地址块 10.0.1.x（掩码 /8）
        # s1连接3个主机
        h1 = self.net.addHost('h1', ip='10.0.1.1/8', mac='00:00:00:00:00:01')
        h2 = self.net.addHost('h2', ip='10.0.1.2/8', mac='00:00:00:00:00:02')
        h3 = self.net.addHost('h3', ip='10.0.1.3/8', mac='00:00:00:00:00:03')
        self.net.addLink(s1, h1, port1=2, port2=1)
        self.net.addLink(s1, h2, port1=3, port2=1)
        self.net.addLink(s1, h3, port1=4, port2=1)
        
        # s2连接2个主机
        h4 = self.net.addHost('h4', ip='10.0.1.4/8', mac='00:00:00:00:00:04')
        h5 = self.net.addHost('h5', ip='10.0.1.5/8', mac='00:00:00:00:00:05')
        self.net.addLink(s2, h4, port1=2, port2=1)
        self.net.addLink(s2, h5, port1=3, port2=1)
        
        # Domain 2: 10.0.2.x/8
        # s3连接3个主机
        h6 = self.net.addHost('h6', ip='10.0.2.1/8', mac='00:00:00:00:00:06')
        h7 = self.net.addHost('h7', ip='10.0.2.2/8', mac='00:00:00:00:00:07')
        h8 = self.net.addHost('h8', ip='10.0.2.3/8', mac='00:00:00:00:00:08')
        self.net.addLink(s3, h6, port1=2, port2=1)
        self.net.addLink(s3, h7, port1=3, port2=1)
        self.net.addLink(s3, h8, port1=4, port2=1)
        
        # s4连接2个主机
        h9 = self.net.addHost('h9', ip='10.0.2.4/8', mac='00:00:00:00:00:09')
        h10 = self.net.addHost('h10', ip='10.0.2.5/8', mac='00:00:00:00:00:0a')
        self.net.addLink(s4, h9, port1=2, port2=1)
        self.net.addLink(s4, h10, port1=3, port2=1)
        
        # Domain 3: 10.0.3.x/8
        # s5连接3个主机
        h11 = self.net.addHost('h11', ip='10.0.3.1/8', mac='00:00:00:00:00:0b')
        h12 = self.net.addHost('h12', ip='10.0.3.2/8', mac='00:00:00:00:00:0c')
        h13 = self.net.addHost('h13', ip='10.0.3.3/8', mac='00:00:00:00:00:0d')
        self.net.addLink(s5, h11, port1=1, port2=1)
        self.net.addLink(s5, h12, port1=2, port2=1)
        self.net.addLink(s5, h13, port1=3, port2=1)
        
        # Domain 4: 10.0.4.x/8
        # s6连接2个主机
        h14 = self.net.addHost('h14', ip='10.0.4.1/8', mac='00:00:00:00:00:0e')
        h15 = self.net.addHost('h15', ip='10.0.4.2/8', mac='00:00:00:00:00:0f')
        self.net.addLink(s6, h14, port1=2, port2=1)
        self.net.addLink(s6, h15, port1=3, port2=1)
        
        # s7连接3个主机
        h16 = self.net.addHost('h16', ip='10.0.4.3/8', mac='00:00:00:00:00:10')
        h17 = self.net.addHost('h17', ip='10.0.4.4/8', mac='00:00:00:00:00:11')
        h18 = self.net.addHost('h18', ip='10.0.4.5/8', mac='00:00:00:00:00:12')
        self.net.addLink(s7, h16, port1=2, port2=1)
        self.net.addLink(s7, h17, port1=3, port2=1)
        self.net.addLink(s7, h18, port1=4, port2=1)
        
        # Domain 5: 10.0.5.x/8
        # s8连接3个主机
        h19 = self.net.addHost('h19', ip='10.0.5.1/8', mac='00:00:00:00:00:13')
        h20 = self.net.addHost('h20', ip='10.0.5.2/8', mac='00:00:00:00:00:14')
        h21 = self.net.addHost('h21', ip='10.0.5.3/8', mac='00:00:00:00:00:15')
        self.net.addLink(s8, h19, port1=1, port2=1)
        self.net.addLink(s8, h20, port1=2, port2=1)
        self.net.addLink(s8, h21, port1=3, port2=1)
        
        # Domain 6: 10.0.6.x/8
        # s9连接2个主机
        h22 = self.net.addHost('h22', ip='10.0.6.1/8', mac='00:00:00:00:00:16')
        h23 = self.net.addHost('h23', ip='10.0.6.2/8', mac='00:00:00:00:00:17')
        self.net.addLink(s9, h22, port1=2, port2=1)
        self.net.addLink(s9, h23, port1=3, port2=1)
        
        # s10连接3个主机
        h24 = self.net.addHost('h24', ip='10.0.6.3/8', mac='00:00:00:00:00:18')
        h25 = self.net.addHost('h25', ip='10.0.6.4/8', mac='00:00:00:00:00:19')
        h26 = self.net.addHost('h26', ip='10.0.6.5/8', mac='00:00:00:00:00:1a')
        self.net.addLink(s10, h24, port1=2, port2=1)
        self.net.addLink(s10, h25, port1=3, port2=1)
        self.net.addLink(s10, h26, port1=4, port2=1)
        
        info('*** 配置交换机与控制器连接\n')
        # 将交换机连接到对应的控制器
        # 使用 ovs-vsctl 明确设置每个交换机的控制器，确保只连接到指定的控制器端口
        
        # Domain 1: s1, s2 -> c1 (127.0.0.1:6654)
        info('配置 Domain 1 交换机连接到控制器 c1 (127.0.0.1:6654)\n')
        s1.start([controllers[0]])
        s2.start([controllers[0]])
        # 明确设置控制器，使用端口号区分
        s1.cmd('ovs-vsctl set-controller s1 tcp:127.0.0.1:6654')
        s2.cmd('ovs-vsctl set-controller s2 tcp:127.0.0.1:6654')
        info('  s1 -> 127.0.0.1:6654\n')
        info('  s2 -> 127.0.0.1:6654\n')
        
        # Domain 2: s3, s4 -> c2 (127.0.0.1:6655)
        info('配置 Domain 2 交换机连接到控制器 c2 (127.0.0.1:6655)\n')
        s3.start([controllers[1]])
        s4.start([controllers[1]])
        s3.cmd('ovs-vsctl set-controller s3 tcp:127.0.0.1:6655')
        s4.cmd('ovs-vsctl set-controller s4 tcp:127.0.0.1:6655')
        info('  s3 -> 127.0.0.1:6655\n')
        info('  s4 -> 127.0.0.1:6655\n')
        
        # Domain 3: s5 -> c3 (127.0.0.1:6656)
        info('配置 Domain 3 交换机连接到控制器 c3 (127.0.0.1:6656)\n')
        s5.start([controllers[2]])
        s5.cmd('ovs-vsctl set-controller s5 tcp:127.0.0.1:6656')
        info('  s5 -> 127.0.0.1:6656\n')
        
        # Domain 4: s6, s7 -> c4 (127.0.0.1:6657)
        info('配置 Domain 4 交换机连接到控制器 c4 (127.0.0.1:6657)\n')
        s6.start([controllers[3]])
        s7.start([controllers[3]])
        s6.cmd('ovs-vsctl set-controller s6 tcp:127.0.0.1:6657')
        s7.cmd('ovs-vsctl set-controller s7 tcp:127.0.0.1:6657')
        info('  s6 -> 127.0.0.1:6657\n')
        info('  s7 -> 127.0.0.1:6657\n')
        
        # Domain 5: s8 -> c5 (127.0.0.1:6658)
        info('配置 Domain 5 交换机连接到控制器 c5 (127.0.0.1:6658)\n')
        s8.start([controllers[4]])
        s8.cmd('ovs-vsctl set-controller s8 tcp:127.0.0.1:6658')
        info('  s8 -> 127.0.0.1:6658\n')
        
        # Domain 6: s9, s10 -> c6 (127.0.0.1:6659)
        info('配置 Domain 6 交换机连接到控制器 c6 (127.0.0.1:6659)\n')
        s9.start([controllers[5]])
        s10.start([controllers[5]])
        s9.cmd('ovs-vsctl set-controller s9 tcp:127.0.0.1:6659')
        s10.cmd('ovs-vsctl set-controller s10 tcp:127.0.0.1:6659')
        info('  s9 -> 127.0.0.1:6659\n')
        info('  s10 -> 127.0.0.1:6659\n')
        
        # 验证连接配置
        info('\n*** 验证交换机控制器连接配置\n')
        for switch_name, expected_port in [('s1', 6654), ('s2', 6654), ('s3', 6655), ('s4', 6655),
                                            ('s5', 6656), ('s6', 6657), ('s7', 6657),
                                            ('s8', 6658), ('s9', 6659), ('s10', 6659)]:
            switch = self.net.get(switch_name)
            result = switch.cmd('ovs-vsctl get-controller %s' % switch_name)
            info('  %s 控制器: %s' % (switch_name, result.strip()))
        
        info('*** 网络拓扑创建完成\n')
        info('拓扑结构 (主机统一 10.0.0.0/8，无默认网关，便于跨域二层互通):\n')
        info('  Domain 1 (Controller: 127.0.0.1:6654, 地址: 10.0.1.x/8):\n')
        info('    s1 <-> s2 (域内)\n')
        info('    s2 <-> s3 (域间: Domain 2)\n')
        info('    s1: h1(10.0.1.1), h2(10.0.1.2), h3(10.0.1.3)\n')
        info('    s2: h4(10.0.1.4), h5(10.0.1.5)\n')
        info('  Domain 2 (Controller: 127.0.0.1:6655, 地址: 10.0.2.x/8):\n')
        info('    s3 <-> s4 (域内)\n')
        info('    s3 <-> s2 (域间: Domain 1)\n')
        info('    s4 <-> s5 (域间: Domain 3)\n')
        info('    s3: h6(10.0.2.1), h7(10.0.2.2), h8(10.0.2.3)\n')
        info('    s4: h9(10.0.2.4), h10(10.0.2.5)\n')
        info('  Domain 3 (Controller: 127.0.0.1:6656, 地址: 10.0.3.x/8):\n')
        info('    s4 <-> s5 (域间: Domain 2)\n')
        info('    s5 <-> s6 (域间: Domain 4)\n')
        info('    s5: h11(10.0.3.1), h12(10.0.3.2), h13(10.0.3.3)\n')
        info('  Domain 4 (Controller: 127.0.0.1:6657, 地址: 10.0.4.x/8):\n')
        info('    s6 <-> s7 (域内)\n')
        info('    s6 <-> s5 (域间: Domain 3)\n')
        info('    s7 <-> s8 (域间: Domain 5)\n')
        info('    s6: h14(10.0.4.1), h15(10.0.4.2)\n')
        info('    s7: h16(10.0.4.3), h17(10.0.4.4), h18(10.0.4.5)\n')
        info('  Domain 5 (Controller: 127.0.0.1:6658, 地址: 10.0.5.x/8):\n')
        info('    s7 <-> s8 (域间: Domain 4)\n')
        info('    s8 <-> s9 (域间: Domain 6)\n')
        info('    s8: h19(10.0.5.1), h20(10.0.5.2), h21(10.0.5.3)\n')
        info('  Domain 6 (Controller: 127.0.0.1:6659, 地址: 10.0.6.x/8):\n')
        info('    s9 <-> s10 (域内)\n')
        info('    s9 <-> s8 (域间: Domain 5)\n')
        info('    s9: h22(10.0.6.1), h23(10.0.6.2)\n')
        info('    s10: h24(10.0.6.3), h25(10.0.6.4), h26(10.0.6.5)\n')
        info('\n域间连接路径: Domain 1 <-> Domain 2 <-> Domain 3 <-> Domain 4 <-> Domain 5 <-> Domain 6 (线性拓扑，无环路)\n')
        
        return self.net


def main():
    """主函数：创建并启动网络"""
    setLogLevel('info')
    
    # 创建拓扑
    topo = ComplexTopo()
    net = topo.build()
    
    # 启动网络
    info('*** 启动网络\n')
    net.start()
    
    # 在启动后再次确认每个交换机的控制器配置
    info('*** 确认交换机控制器连接\n')
    switch_controller_map = {
        's1': 6654, 's2': 6654,  # Domain 1
        's3': 6655, 's4': 6655,  # Domain 2
        's5': 6656,              # Domain 3
        's6': 6657, 's7': 6657,  # Domain 4
        's8': 6658,              # Domain 5
        's9': 6659, 's10': 6659  # Domain 6
    }
    
    for switch_name, expected_port in switch_controller_map.items():
        switch = net.get(switch_name)
        # 确保每个交换机只连接到指定的控制器端口
        switch.cmd('ovs-vsctl set-controller %s tcp:127.0.0.1:%d' % (switch_name, expected_port))
        # 获取并显示当前控制器配置
        controller_info = switch.cmd('ovs-vsctl get-controller %s' % switch_name).strip()
        info('  %s -> %s (期望: tcp:127.0.0.1:%d)\n' % (switch_name, controller_info, expected_port))
    
    # 启动CLI界面
    info('*** 启动Mininet CLI\n')
    info('说明: 各控制器需安装可泛洪/学习 MAC 的应用(如 simple_switch_13)，\n')
    info('      以便 ARP 与未知单播经域间链路互通。\n')
    info('可以使用以下命令测试网络:\n')
    info('  pingall          # 测试所有主机之间的连通性\n')
    info('  h1 ping h2       # 测试特定主机之间的连通性\n')
    info('  net              # 显示网络拓扑\n')
    info('  nodes            # 显示所有节点\n')
    info('  links            # 显示所有链路\n')
    info('  dump             # 显示网络详细信息\n')
    info('  exit             # 退出CLI\n')
    
    CLI(net)
    
    # 清理网络
    info('*** 停止网络\n')
    net.stop()


if __name__ == '__main__':
    main()

