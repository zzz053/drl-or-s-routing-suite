"""
该文件从原始 controller 大文件中拆分了通用辅助能力，
主要是 ARP 相关的可复用逻辑。

函数作用：
- get_loop_safe_arp_flood_ports(...)：
  选择 ARP 广播端口（不使用生成树），仅过滤入端口和非法端口。

ARP 报文去重已迁至 TopoAwareness._arp_dedup_should_drop（按 dpid、源 MAC/IP、opcode）。
"""

import time

from ryu.ofproto import ofproto_v1_3


def get_loop_safe_arp_flood_ports(
    dpid,
    in_port,
    switch_mac_to_port,
    topo_inter_link,
    topo_access_link,
    is_link_port_fn,
    get_port_from_link_fn,
):
    """
    选择 ARP 泛洪端口（不使用生成树）。
    当前策略：除入端口与非法端口外，其余端口均可泛洪。
    """
    all_ports = list(switch_mac_to_port.get(dpid, {}).keys())
    candidate_ports = []
    for p in all_ports:
        if p == in_port:
            continue
        if p >= ofproto_v1_3.OFPP_MAX:
            continue
        candidate_ports.append(p)
    return sorted(candidate_ports)


def l4_reverse_for_match(l4_fwd):
    """
    将正向流上的 L4 匹配字段（tcp_* / udp_*）交换为反向流所用。
    l4_fwd 为 None 或非 TCP/UDP 时返回 None。
    """
    if not l4_fwd:
        return None
    rev = {'ip_proto': l4_fwd['ip_proto']}
    if 'tcp_src' in l4_fwd:
        rev['tcp_src'] = l4_fwd['tcp_dst']
        rev['tcp_dst'] = l4_fwd['tcp_src']
        return rev
    if 'udp_src' in l4_fwd:
        rev['udp_src'] = l4_fwd['udp_dst']
        rev['udp_dst'] = l4_fwd['udp_src']
        return rev
    return None
