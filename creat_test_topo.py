#!/usr/bin/python3

import itertools
import random
import time

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel


def staggered_pingall(net, interval=0.3, bidirectional=False, count=1, timeout=1):
    """
    以可控间隔执行“类 pingall”测试，降低并发冲击。

    Args:
        net: Mininet 实例
        interval: 每次 ping 之间的间隔秒数
        bidirectional: True 时对每对主机执行 A->B 与 B->A
        count: 每次 ping 的发送包数（-c）
        timeout: 单次 ping 超时（-W，秒）

    Returns:
        dict: {'total': 总测试数, 'success': 成功数, 'failed': 失败数}
    """
    hosts = list(net.hosts)
    total = 0
    success = 0

    def _run_one(src, dst):
        nonlocal total, success
        total += 1
        cmd = "ping -c {count} -W {timeout} {dst_ip}".format(
            count=int(count), timeout=int(timeout), dst_ip=dst.IP()
        )
        out = src.cmd(cmd)
        ok = (" 0% packet loss" in out) and ("100% packet loss" not in out)
        status = "OK" if ok else "FAIL"
        print("[{status}] {src} -> {dst}".format(status=status, src=src.name, dst=dst.name))
        if ok:
            success += 1
        time.sleep(max(float(interval), 0.0))

    # 主机对随机打散，避免每次固定顺序造成测试偏置
    host_pairs = list(itertools.combinations(hosts, 2))
    random.shuffle(host_pairs)

    for h1, h2 in host_pairs:
        _run_one(h1, h2)
        if bidirectional:
            _run_one(h2, h1)

    failed = total - success
    print("\n=== staggered_pingall 结果 ===")
    print("total={0}, success={1}, failed={2}, loss={3:.2f}%".format(
        total, success, failed, (failed * 100.0 / total) if total else 0.0
    ))
    return {'total': total, 'success': success, 'failed': failed}


def create_topology():
    net = Mininet(controller=None, switch=OVSSwitch)

    # ==================== 域定义区 ====================
    # 域1：扩展环（不挂主机）
    domain1_switches = list(range(1, 7)) + [45, 46, 47]
    # 域2：三节点环
    domain2_switches = [42, 43, 44]
    # 域3：7节点树
    domain3_switches = [28, 31, 30, 29, 34, 33, 32]

    # 域4~域7：与域3同构，根节点固定
    domain_roots = [7, 14, 21, 35]
    domain_tree_nodes = {
        root: [root, root + 1, root + 2, root + 3, root + 4, root + 5, root + 6]
        for root in domain_roots
    }

    # 域控制器端口映射
    controller_specs = {
        'c1': 6654,
        'c2': 6655,
        'c3': 6656,
        'c4': 6657,
        'c5': 6658,
        'c6': 6659,
        'c7': 6670,
    }

    # 域与控制器绑定关系（仅用于交换机 start）
    domain_to_ctrl = {
        'domain1': 'c1',
        'domain2': 'c2',
        'domain3': 'c3',
        'domain4': 'c4',  # root=7
        'domain5': 'c5',  # root=14
        'domain6': 'c6',  # root=21
        'domain7': 'c7',  # root=35
    }

    # ==================== 创建控制器 ====================
    print("=== 创建控制器 ===")
    controllers = {}
    for name, port in controller_specs.items():
        controllers[name] = net.addController(
            name,
            controller=RemoteController,
            ip='127.0.0.1',
            port=port
        )

    # ==================== 创建交换机 ====================
    print("=== 创建交换机 ===")
    switches = {}
    all_switch_nums = (
        domain1_switches
        + domain2_switches
        + domain3_switches
        + [num for nodes in domain_tree_nodes.values() for num in nodes]
    )
    for i in all_switch_nums:
        switches[f's{i}'] = net.addSwitch(f's{i}')

    # ==================== 创建主机 ====================
    # 域1和域2（三节点环）不挂测试主机，其余交换机均挂一个主机
    print("=== 创建主机并连接交换机（域1、域2不挂主机）===")
    hosts = {}
    no_host_switch_set = set(domain1_switches + domain2_switches)
    for sw_name, sw in switches.items():
        num = int(sw_name[1:])
        if num in no_host_switch_set:
            continue
        host = net.addHost(f'h{num}', ip=f'10.0.0.{num}')
        hosts[f'h{num}'] = host
        net.addLink(host, sw)

    # ==================== 构建域内链路 ====================
    print("=== 构建域内链路 ===")
    # 域1：基础环 + 扩展结构
    domain1_intra_links = [
        (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1),
        (46, 3), (46, 4), (46, 5), (46, 47),
        (45, 1), (45, 2), (45, 3), (45, 47),
        (47, 1), (47, 6),
    ]
    # 域2：环
    domain2_intra_links = [(42, 43), (43, 44), (44, 42)]
    # 域3：树
    domain3_intra_links = [(28, 31), (28, 30), (28, 29), (31, 34), (30, 33), (29, 32)]

    for a, b in domain1_intra_links + domain2_intra_links + domain3_intra_links:
        net.addLink(switches[f's{a}'], switches[f's{b}'])

    # 域4~域7：与域3同构树
    for root in domain_roots:
        r = root
        c_a, c_b, c_c = r + 1, r + 2, r + 3
        l_a, l_b, l_c = r + 4, r + 5, r + 6
        for a, b in [(r, c_a), (r, c_b), (r, c_c), (c_a, l_a), (c_b, l_b), (c_c, l_c)]:
            net.addLink(switches[f's{a}'], switches[f's{b}'])

    # ==================== 构建域间链路 ====================
    print("=== 构建域间链路 ===")
    inter_domain_links = [
        # 已有域2/域3接入域1
        (42, 2), (42, 3),
        (28, 1), (28, 6),
        # 新增域4~域7接入域1
        (7, 3), (7, 4),
        (14, 4), (14, 5),
        (21, 5), (21, 6),
        (35, 1), (35, 2),
    ]
    for a, b in inter_domain_links:
        net.addLink(switches[f's{a}'], switches[f's{b}'])

    # ==================== 启动并绑定控制器 ====================
    print("=== 启动网络 ===")
    net.start()

    # 挂载到 net，确保在 Mininet CLI 的 py 环境可直接调用
    net.staggered_pingall = lambda interval=0.3, bidirectional=False, count=1, timeout=1: (
        staggered_pingall(
            net,
            interval=interval,
            bidirectional=bidirectional,
            count=count,
            timeout=timeout,
        )
    )

    print("=== 绑定交换机到控制器 ===")
    domain_switch_map = {
        'domain1': domain1_switches,
        'domain2': domain2_switches,
        'domain3': domain3_switches,
        'domain4': domain_tree_nodes[7],
        'domain5': domain_tree_nodes[14],
        'domain6': domain_tree_nodes[21],
        'domain7': domain_tree_nodes[35],
    }
    for domain_name, switch_nums in domain_switch_map.items():
        ctrl_name = domain_to_ctrl[domain_name]
        ctrl = controllers[ctrl_name]
        for i in switch_nums:
            switches[f's{i}'].start([ctrl])

    print("=== 网络已启动，进入 CLI ===")
    print("可在 CLI 执行：py net.staggered_pingall(interval=0.3, bidirectional=False)")
    CLI(net)

    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    create_topology()