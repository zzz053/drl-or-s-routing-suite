import time
import json
import socket
import logging
import hashlib
import threading

import netifaces
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3, ether
from ryu.lib.packet import ethernet, ether_types, arp, packet
from ryu.lib import hub
from ryu.topology import event
from ryu.topology.switches import LLDPPacket
from ryu.base.app_manager import lookup_service_brick
import networkx as nx
from operator import attrgetter
from common_config import (
    SERVER_CONFIG,
    HOST_PORT_TASK_RANGES,
    TASK_POLICY_MAP,
    TASK_PRIORITY_MAP,
    ROUTE_FLOW_IDLE_TIMEOUT,
    ROUTE_FLOW_HARD_TIMEOUT,
    FLOW_INSTALL_BARRIER_TIMEOUT,
    EXTERNAL_LINK_PORTS,
    EXTERNAL_ARP_ALLOWED_PREFIXES,
    VIRTUAL_SWITCH_DPID_MAX,
)
from host_model import Host
from controller_helpers import (
    get_loop_safe_arp_flood_ports,
    l4_reverse_for_match,
)
from external_host_guard import (
    is_external_host_source,
    purge_virtual_host_records_for_source,
    remember_external_host_source,
    should_skip_external_host_learning,
    should_drop_external_arp,
)
from packetin_lldp import handle_lldp_packet_in
from packetin_arp import handle_switch_packet_in, handle_host_arp_packet_in
from packetin_ip import handle_host_ip_packet_in

Initial_bandwidth = 800

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),  # 输出到控制台
    ]
)
logger = logging.getLogger("server_agent")

class TopoAwareness(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TopoAwareness, self).__init__(*args, **kwargs)
        self.name = 'topo_awareness'
        self.topology_api_app = self  # 将当前实例赋值给属性
        self.local_mac = ''
        # Link、switch and host
        self.dpid_to_switch_ip = {}
        self.dpid_to_switch = {}  # Store switch in topology using OpenFlow
        self.switch_mac_to_port = {}  # {dpid:{port1:hw_addr1,port2:hwaddr2,...},...}嵌套字典,外层键(dpid)和内层键(port1)
        self.host_to_sw_port = {}  # {dpid1:{port1:[mac, ipv4],port2:[mac,ipv4]...},...}嵌套字典
        self.remote_hosts = {}  # {ip: {'mac': ..., 'dpid': ..., 'port': ...}}，仅用于观测，不参与本地路径判断
        self.external_host_sources = set()  # {(mac, ip)} learned from configured physical/external ports
        self.topo_inter_link = {}  # {(src.dpid, dst.dpid): (src.port_no, timestamp, delay, bw, loss)}存储交换机之间的内部链路信息.包括端口、时间戳、延迟、带宽和丢包率。
        self.topo_access_link = {}  # 存储接入链路信息（域间交换机的链路）
        # 永久链路口集合：一旦某端口曾确认连接过交换机，则永久视为链路口，不再允许学习主机
        self.permanent_link_ports = {}  # {dpid: set(port_no, ...)}
        # self.detection_access_link = {}  # 带有时间戳的外部链路信息，用于超时检测，超时检测后被赋值给真正的外部链路
        # self.detection_inter_link = {}

        # calculate delay
        self.echo_timestamp = {} # {dpid:recvtime,1:0.5,2:0.3,....}控制器收到交换机echo回复的时间戳，根据时间是否超过30秒交换机是否断开连接
        self.echo_latency = {}  # {dpid:delaytime,1:0.5,2:0.3,....}每个交换机与控制器之间的Echo时延
        self.lldp_delay = {}  # {(src,dst):time,(1,2):0.5,...}
        self.link_delay = {}  # {(src,dst):time,(1,2):0.5,....}交换机之间链路的延迟时间
        
        # 用于存储待处理的PortData查询请求（等待server响应）
        # key: (src_dpid, src_port_no, dst_dpid), value: (接收LLDP包的时间戳, 查询请求时间戳)
        self.pending_portdata_queries = {}

        # calculate bw
        self.port_stats = {}
        self.free_bandwidth = {}  # {dpid: {port_no: (free_bandwidth, usage), ...}, ...} (Mbit/s),每个交换机端口的带宽使用情况
        self.port_loss_stats = {}  # 新增的端口丢包统计字典

        ###########
        self.mac_to_port = {}
        self.arp_table = {}  # ARP表{ (dpid, src_mac, dst_ip):in_port }
        # ARP去重表 {(dpid, src_mac, src_ip, dst_ip, opcode): timestamp}，防止有环拓扑广播风暴
        self._arp_seen = {}
        self._arp_seen_ttl = 5.0

        self.graph = nx.DiGraph()  # graph用于存储网络拓扑的图结构,用 networkx 库中的 DiGraph 类创建了一个有向图。
        

        #各种标志位的开关
        self.show_enable = True  # 控制show方法的开关，True为开启，False为关闭
        self.host_migration_log_enable = True  # 控制主机迁移相关日志的开关
        self.ip_packet_log_enable = False  # 控制IP数据包日志的开关
        # PacketIn 观测开关：需要关闭时改为 False 或直接注释此行
        self.packet_watch_log_enable = True
        self.packet_watch_prefix = "<<<PACKET-WATCH>>>"
        # 抓包模式开关：开启后禁止 ARP/IP 直通流表下发，强制每包继续上送控制器
        # 不需要时改为 False，或直接注释这一行
        self.packet_capture_mode_enable = False
        
        # 获取switches实例，用于访问PortData中的时间戳和echo延迟
        self.switches = None

        self.update_thread = hub.spawn(self.link_timeout_detection, self.topo_access_link)
        self.measure_thread = hub.spawn(self._detector)   # 启动一个线程，定期执行网络指标的测量任务
        self.monitor_thread = hub.spawn(self._monitor_thread)  # 启动一个线程，定期执行网络监控任务(未启用)

        self.show_info = hub.spawn(self.show)   
        self.check_switch_thread = hub.spawn(self._check_switch_state, self.echo_timestamp)
        self.get_mac_thread = hub.spawn(self.get_local_mac_address)

        # 添加server连接相关的属性
        self.server_socket = None
        self.is_connected = False
        self.server_addr = (SERVER_CONFIG['server_ip'], SERVER_CONFIG['server_port'])
        self._send_lock = threading.Lock()
        self._recv_greenlet = None
        self._path_requested = {}
        self._pending_path_packets = {}

        self._apply_configured_external_link_ports()
        # 交换机真实流表快照（来自 OFPFlowStatsReply）
        self.switch_flow_stats = {}  # {dpid: [flow_entry, ...]}
        # 已下发路径会话：用于链路断开后的相关流表删除
        self.route_sessions = {}  # {session_id: {'path': [...], 'links': set(), 'flows': [...]}}
        self._next_route_session_id = 1
        self._route_session_sid_hints = {}  # {session_key: sid}，用于重路由后复用会话ID
        self._active_flow_tracking = None
        self._barrier_events = {}
        
        # 启动server连接线程
        self.connect_thread = hub.spawn(self._connect_to_server)
        self.topo_update_thread = hub.spawn(self._send_topo_loop)
        self.heartbeat_thread = hub.spawn(self._heartbeat_loop)

    def get_local_mac_address(self):
        # 使用 netifaces 库获取本地设备上网络接口信息,获取本地MAC地址
        interfaces = netifaces.interfaces()

        # 遍历接口并获取 MAC 地址
        for interface in interfaces:
            if interface == 'lo':  # 回环接口 lo 通常用于本地回环测试，不包含实际的物理MAC地址，因此需要跳过。
                continue  # 跳过回环接口
            try:
                self.local_mac = netifaces.ifaddresses(interface)[netifaces.AF_LINK][0]['addr']  # 从返回的字典中提取 MAC 地址信息
                break                                                                            # 从(列表中的第一个元素)第一个地址字典中提取 addr 键的值
            except KeyError:
                pass

    """
        收集网络带宽信息
    """
    # 定期请求交换机的端口统计信息，计算带宽使用情况，并保存到数据结构中。
    def _monitor_thread(self):
        while True:
            self._request_stats()
            self.add_bandwidth_info(self.free_bandwidth)  # 将当前的各个端口的可用带宽信息传递给 add_bandwidth_info 方法,实时更新带宽的使用情况
            hub.sleep(1.2)

    # Stat request: 向网络中的交换机请求端口统计信息
    def _request_stats(self):
        datapaths = list(self.dpid_to_switch.values())
        for datapath in datapaths:
            self.logger.debug('send stats request: %016x', datapath.id)
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)  # 创建一个 OpenFlow 消息，用于请求特定交换机的所有端口统计信息
            datapath.send_msg(req)  # 将创建的统计请求消息发送到对应的交换机
            flow_req = parser.OFPFlowStatsRequest(datapath)
            datapath.send_msg(flow_req)
            hub.sleep(0.5)

    @staticmethod
    def _format_match_to_dict(match_obj):
        out = {}
        fields = match_obj.to_jsondict().get('OFPMatch', {}).get('oxm_fields', [])
        for item in fields:
            if isinstance(item, dict) and 'OXMTlv' in item:
                tlv = item['OXMTlv']
                field = tlv.get('field')
                if field is not None:
                    out[field] = tlv.get('value')
        return out

    @staticmethod
    def _format_actions_to_text(instructions):
        action_names = []
        for inst in instructions or []:
            actions = getattr(inst, 'actions', None)
            if not actions:
                continue
            for action in actions:
                if hasattr(action, 'port'):
                    action_names.append(f"OUTPUT:{action.port}")
                else:
                    action_names.append(type(action).__name__)
        return ','.join(action_names) if action_names else 'N/A'

    @staticmethod
    def _serialize_match_for_delete(match_obj):
        fields = match_obj.to_jsondict().get('OFPMatch', {}).get('oxm_fields', [])
        out = {}
        for item in fields:
            if isinstance(item, dict) and 'OXMTlv' in item:
                tlv = item['OXMTlv']
                field = tlv.get('field')
                if field is not None:
                    out[field] = tlv.get('value')
        return out

    @staticmethod
    def _flow_record_matches(dpid, priority, match_dict, flow_record):
        return (
            flow_record.get('dpid') == dpid and
            int(flow_record.get('priority', 0)) == int(priority) and
            flow_record.get('match', {}) == (match_dict or {})
        )

    def _wait_for_flow_barriers(self, datapaths, timeout=FLOW_INSTALL_BARRIER_TIMEOUT):
        ok = True
        for datapath in datapaths:
            req = None
            try:
                req = datapath.ofproto_parser.OFPBarrierRequest(datapath)
                datapath.set_xid(req)
                waiter = hub.Event()
                self._barrier_events[(datapath.id, req.xid)] = waiter
                datapath.send_msg(req)
                if waiter.wait(timeout=timeout) is not True:
                    ok = False
                    self.logger.warning("[Path] flow barrier timeout: dpid=%s xid=%s",
                                        datapath.id, req.xid)
            except Exception as exc:
                ok = False
                self.logger.warning("[Path] flow barrier wait failed: dpid=%s error=%s",
                                    getattr(datapath, 'id', None), exc)
            finally:
                if req is not None:
                    self._barrier_events.pop((getattr(datapath, 'id', None), getattr(req, 'xid', None)), None)
        return ok

    def _begin_flow_tracking(self):
        self._active_flow_tracking = []

    def _end_flow_tracking(self):
        records = self._active_flow_tracking if self._active_flow_tracking is not None else []
        self._active_flow_tracking = None
        return records

    @staticmethod
    def _normalize_l4_match_for_session(l4_match):
        if not l4_match or not isinstance(l4_match, dict):
            return None
        out = {}
        for k, v in l4_match.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                return None
        return out

    @staticmethod
    def _serialize_session_l4_key(l4_match):
        return json.dumps(l4_match or {}, sort_keys=True, ensure_ascii=False)

    def _build_route_session_key(self, path, meta, normalized_l4):
        return (
            meta.get('src_ip'),
            meta.get('dst_ip'),
            meta.get('task_type', 'default'),
            meta.get('route_policy', 'shortest_path'),
            self._serialize_session_l4_key(normalized_l4),
        )

    def _find_existing_session_id(self, session_key):
        src_ip, dst_ip, task_type, route_policy, l4_key = session_key
        for sid, session in self.route_sessions.items():
            if session.get('session_key') == session_key:
                return sid
            # 兼容旧记录（没有 session_key 字段）做一次回退匹配
            old_l4_key = self._serialize_session_l4_key(session.get('l4_match'))
            if (
                session.get('src_ip') == src_ip and
                session.get('dst_ip') == dst_ip and
                session.get('task_type', 'default') == task_type and
                session.get('route_policy', 'shortest_path') == route_policy and
                old_l4_key == l4_key
            ):
                return sid
        return None

    def _record_route_session(self, path, flow_records, session_meta=None, preferred_sid=None):
        if not path or not flow_records:
            return
        links = set()
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            if isinstance(a, int) and isinstance(b, int):
                links.add((a, b) if a <= b else (b, a))
        if not links:
            return
        meta = session_meta if isinstance(session_meta, dict) else {}
        normalized_l4 = self._normalize_l4_match_for_session(meta.get('l4_match'))
        session_key = self._build_route_session_key(path, meta, normalized_l4)
        sid = self._find_existing_session_id(session_key)
        if sid is None and preferred_sid is not None:
            sid = int(preferred_sid)
        if sid is None:
            hinted_sid = self._route_session_sid_hints.pop(session_key, None)
            if hinted_sid is not None:
                sid = int(hinted_sid)
        old = self.route_sessions.get(sid) if sid is not None else None
        if sid is None:
            sid = self._next_route_session_id
            self._next_route_session_id += 1
        now_ts = time.time()
        self.route_sessions[sid] = {
            'path': list(path),
            'links': links,
            'flows': flow_records,
            'created_at': old.get('created_at', now_ts) if old else now_ts,
            'updated_at': now_ts,
            'src_ip': meta.get('src_ip'),
            'dst_ip': meta.get('dst_ip'),
            'task_type': meta.get('task_type', 'default'),
            'route_policy': meta.get('route_policy', 'shortest_path'),
            'l4_match': normalized_l4,
            'switch_id': meta.get('switch_id'),
            'in_port': meta.get('in_port'),
            'path_source': meta.get('path_source', 'unknown'),
            'decision_source': meta.get('decision_source', meta.get('path_source', 'unknown')),
            'model_used': meta.get('model_used', False),
            'fallback_reason': meta.get('fallback_reason'),
            'model_confidence': meta.get('model_confidence'),
            'drl_compute_time': meta.get('drl_compute_time'),
            'drl_shadow': meta.get('drl_shadow'),
            'session_key': session_key,
        }
        # 控制上限，避免长期运行内存增长
        if len(self.route_sessions) > 500:
            oldest = sorted(self.route_sessions.keys())[0]
            self.route_sessions.pop(oldest, None)

    def _delete_tracked_flow(self, dpid, priority, match_dict):
        datapath = self.dpid_to_switch.get(dpid)
        if datapath is None:
            return
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        try:
            match = parser.OFPMatch(**(match_dict or {}))
        except Exception:
            self.logger.warning("重路由删流构造 match 失败: dpid=%s, match=%s", dpid, match_dict)
            return
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE_STRICT,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            priority=int(priority),
            match=match
        )
        datapath.send_msg(mod)

    def _remove_flow_from_sessions(self, dpid, priority, match_dict):
        removed_sessions = []
        for sid, session in list(self.route_sessions.items()):
            flows = session.get('flows', []) or []
            if any(self._flow_record_matches(dpid, priority, match_dict, flow) for flow in flows):
                removed_sessions.append(sid)
                self.route_sessions.pop(sid, None)
        return removed_sessions

    def _notify_link_state(self, state_type, src_dpid, dst_dpid):
        self._send_to_server({
            "type": state_type,
            "src": src_dpid,
            "dst": dst_dpid,
        })

    def _reroute_session(self, session):
        """
        旧路径失效后，尝试重新计算并下发新路径。
        优先域内本地重算；无法本地重算时回退为向根控重新请求全局路径。
        """
        src_ip = session.get('src_ip')
        dst_ip = session.get('dst_ip')
        if not src_ip or not dst_ip:
            return False

        task_type = session.get('task_type', 'default')
        route_policy = session.get('route_policy', self._get_policy_for_task(task_type))
        l4_fwd = self._coerce_l4_match_dict(session.get('l4_match'))
        preferred_sid = session.get('session_id')

        # 抓包模式下保持“每包上送控制器”，不主动重下发路径流表。
        if self.is_packet_capture_mode():
            self.logger.info(
                "%s capture_mode=on skip_reroute_install src_ip=%s dst_ip=%s",
                self.packet_watch_prefix, src_ip, dst_ip
            )
            return False

        src_switch_id = self.get_switch_id_by_ip(src_ip)
        dst_switch_id = self.get_switch_id_by_ip(dst_ip)
        src_port = self.get_switch_port_by_ip(src_ip)

        # 两端主机都在本控制器可见，优先走本地重算。
        if src_switch_id is not None and dst_switch_id is not None and src_port is not None:
            path = self.get_path(src_switch_id, dst_switch_id)
            if not path:
                # 兜底：沿用原逻辑尝试直连路径
                path = [src_switch_id, dst_switch_id]
            self._begin_flow_tracking()
            try:
                self.install_flow_entry(
                    path, src_ip, dst_ip, src_port, msg=None,
                    task_type=task_type, l4_fwd=l4_fwd
                )
            finally:
                flow_records = self._end_flow_tracking()
                self._record_route_session(path, flow_records, {
                    'src_ip': src_ip,
                    'dst_ip': dst_ip,
                    'task_type': task_type,
                    'route_policy': route_policy,
                    'l4_match': l4_fwd,
                    'switch_id': src_switch_id,
                    'in_port': src_port,
                }, preferred_sid=preferred_sid)
            self.logger.info("链路故障后本地重路由完成: %s -> %s, path=%s", src_ip, dst_ip, path)
            return True

        # 跨域或目的不在本地时，向根控重新请求路径。
        request_switch_id = src_switch_id if src_switch_id is not None else session.get('switch_id')
        request_in_port = src_port if src_port is not None else session.get('in_port')
        if self.is_connected and request_switch_id is not None and request_in_port is not None:
            self._request_path(
                src_ip, dst_ip, request_switch_id, request_in_port, msg=None,
                task_type=task_type, route_policy=route_policy, l4_fwd=l4_fwd,
                session_id=preferred_sid
            )
            self.logger.info(
                "链路故障后已向根控请求重路由: %s -> %s, switch=%s, in_port=%s",
                src_ip, dst_ip, request_switch_id, request_in_port
            )
            return True

        self.logger.warning(
            "链路故障后重路由失败（缺少必要上下文）: src_ip=%s dst_ip=%s src_sw=%s dst_sw=%s src_port=%s",
            src_ip, dst_ip, src_switch_id, dst_switch_id, src_port
        )
        return False

    def _invalidate_sessions_on_link_failure(self, src_dpid, dst_dpid):
        edge_key = (src_dpid, dst_dpid) if src_dpid <= dst_dpid else (dst_dpid, src_dpid)
        impacted = []
        for sid, session in self.route_sessions.items():
            if edge_key in session.get('links', set()):
                impacted.append(sid)
        if not impacted:
            return
        removed_flow_count = 0
        reroute_candidates = {}
        for sid in impacted:
            session = self.route_sessions.get(sid, {})
            for flow in session.get('flows', []):
                self._delete_tracked_flow(
                    dpid=flow.get('dpid'),
                    priority=flow.get('priority', 1),
                    match_dict=flow.get('match', {}),
                )
                removed_flow_count += 1
            key = (
                session.get('src_ip'),
                session.get('dst_ip'),
                session.get('task_type', 'default'),
                session.get('route_policy', 'shortest_path'),
                json.dumps(session.get('l4_match') or {}, sort_keys=True, ensure_ascii=False),
            )
            if key[0] and key[1]:
                session_copy = dict(session)
                session_copy['session_id'] = sid
                reroute_candidates[key] = session_copy
                self._route_session_sid_hints[key] = sid
            self.route_sessions.pop(sid, None)
        reroute_ok = 0
        for session in reroute_candidates.values():
            if self._reroute_session(session):
                reroute_ok += 1
        self.logger.warning(
            "检测到链路断开(%s<->%s)，删除受影响流表 %s 条，失效会话 %s 个，重路由触发 %s 条",
            src_dpid, dst_dpid, removed_flow_count, len(impacted), reroute_ok
        )

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        flow_entries = []
        for stat in ev.msg.body:
            # 过滤 table-miss（通常 priority=0 且匹配为空），减少噪音
            if getattr(stat, 'priority', 0) == 0:
                continue

            match_dict = self._format_match_to_dict(stat.match)
            action_text = self._format_actions_to_text(stat.instructions)
            flow_key = json.dumps({
                'priority': int(getattr(stat, 'priority', 0)),
                'match': match_dict,
                'action': action_text
            }, sort_keys=True, ensure_ascii=False)
            flow_id = hashlib.md5(flow_key.encode('utf-8')).hexdigest()[:12]

            flow_entries.append({
                'id': flow_id,
                'priority': int(getattr(stat, 'priority', 0)),
                'match': json.dumps(match_dict, ensure_ascii=False, sort_keys=True),
                'match_dict': match_dict,
                'action': action_text,
                'packets': int(getattr(stat, 'packet_count', 0)),
                'bytes': int(getattr(stat, 'byte_count', 0)),
                'duration_sec': int(getattr(stat, 'duration_sec', 0)),
                'duration_nsec': int(getattr(stat, 'duration_nsec', 0)),
                'idle_timeout': int(getattr(stat, 'idle_timeout', 0)),
                'hard_timeout': int(getattr(stat, 'hard_timeout', 0)),
                'real': True,
            })
        self.switch_flow_stats[dpid] = flow_entries

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def _barrier_reply_handler(self, ev):
        msg = ev.msg
        key = (msg.datapath.id, msg.xid)
        waiter = self._barrier_events.pop(key, None)
        if waiter is not None:
            if hasattr(waiter, 'send'):
                waiter.send(True)
            else:
                waiter.set()

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def _flow_removed_handler(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        priority = int(getattr(msg, 'priority', 0))
        match_dict = self._format_match_to_dict(msg.match)
        reason_map = {
            getattr(msg.datapath.ofproto, 'OFPRR_IDLE_TIMEOUT', 0): 'idle_timeout',
            getattr(msg.datapath.ofproto, 'OFPRR_HARD_TIMEOUT', 1): 'hard_timeout',
            getattr(msg.datapath.ofproto, 'OFPRR_DELETE', 2): 'delete',
            getattr(msg.datapath.ofproto, 'OFPRR_GROUP_DELETE', 3): 'group_delete',
        }
        reason = reason_map.get(getattr(msg, 'reason', None), str(getattr(msg, 'reason', 'unknown')))

        before = len(self.switch_flow_stats.get(dpid, []))
        self.switch_flow_stats[dpid] = [
            flow for flow in self.switch_flow_stats.get(dpid, [])
            if not (
                int(flow.get('priority', 0)) == priority and
                flow.get('match_dict', {}) == match_dict
            )
        ]
        removed_sessions = self._remove_flow_from_sessions(dpid, priority, match_dict)

        self._send_to_server({
            "type": "flow_removed",
            "switch_id": dpid,
            "priority": priority,
            "match": match_dict,
            "reason": reason,
            "packet_count": int(getattr(msg, 'packet_count', 0)),
            "byte_count": int(getattr(msg, 'byte_count', 0)),
            "removed_sessions": removed_sessions,
        })
        self.logger.info(
            "flow_removed: dpid=%s priority=%s reason=%s cached_removed=%s sessions_removed=%s match=%s",
            dpid, priority, reason, before - len(self.switch_flow_stats.get(dpid, [])),
            removed_sessions, match_dict
        )

    def _save_stats(self, _dict, key, value, history_length=2):  # 将统计数据以键值对的形式存储，并限制历史记录的长度
        if key not in _dict:
            _dict[key] = []

        _dict[key].append(value)  # 将 value 添加到 _dict[key] 列表的末尾

        if len(_dict[key]) > history_length:  # 用于指定保留的历史记录长度
            _dict[key].pop(0)   # 检查 _dict[key] 列表的长度。如果超过 history_length，则使用 pop(0) 方法删除列表的第一个元素（最旧的记录）

    def _cal_speed(self, now, pre, period):
        if period:
            return (now - pre) / (period)
            
        else:
            return 0

    def _get_period(self, curr_time, pre_time):
        period = curr_time - pre_time
        return period

    # Bandwidth graph:
    def _save_freebandwidth(self, dpid, port_no, speed):
        capacity = Initial_bandwidth  # Kbp/s to Mbit/s
        speed = float(speed * 8) / (10 ** 6)  # byte/s to Mbit/s
        curr_bw = max(capacity - speed, 0)
        self.free_bandwidth[dpid].setdefault(port_no, None)
        self.free_bandwidth[dpid][port_no] = (curr_bw, speed)  # Save as Mbit/s

    def add_bandwidth_info(self, free_bandwidth):
        """
            Save bandwidth data into networkx graph object.
        """
        link_to_port = self.topo_inter_link
        for link in link_to_port.keys():
            (src_dpid, dst_dpid) = link
            (src_port, _, _, _, _) = link_to_port[link]
            try:
                src_free_bandwidth, _ = free_bandwidth[src_dpid][src_port]
                self.topo_inter_link[(src_dpid, dst_dpid)][3] = src_free_bandwidth
                self.graph[src_dpid][dst_dpid]['free_bandwith'] = src_free_bandwidth
            except:
                pass

        link_to_port = self.topo_access_link
        for link in link_to_port.keys():
            (src_dpid, dst_dpid) = link
            (src_port, _, _, _, _) = link_to_port[link]

            try:
                src_free_bandwidth, _ = free_bandwidth[src_dpid][src_port]
                self.topo_access_link[(src_dpid, dst_dpid)][3] = src_free_bandwidth
                self.graph[src_dpid][dst_dpid]['free_bandwith'] = src_free_bandwidth
            except:
                pass
    # 处理来自交换机的端口统计信息，计算端口的速度，并更新带宽使用情况。
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        """
            保存端口统计信息
            计算端口速度并保存
            计算丢包率并保存
        """
        body = ev.msg.body
        dpid = ev.msg.datapath.id

        # 添加显示统计信息的日志
        # self.logger.info("\n=== Switch %s Port Statistics ===", dpid)
        # self.logger.info("Port     Rx-Pkts     Tx-Pkts     Rx-Bytes     Tx-Bytes     Rx-Dropped  Tx-Dropped")
        # self.logger.info("----     -------     -------     --------     --------     ----------  ----------")
# 
        self.free_bandwidth.setdefault(dpid, {})
        self.port_loss_stats.setdefault(dpid, {})
        now_timestamp = time.time()

        for stat in sorted(body, key=attrgetter('port_no')):
            port_no = stat.port_no
            if port_no != ofproto_v1_3.OFPP_LOCAL:
                # 显示原始统计数据
                # self.logger.info("%4d  %10d  %10d  %10d  %10d  %10d  %10d",
                            #    port_no,
                            #    stat.rx_packets, stat.tx_packets,
                            #    stat.rx_bytes, stat.tx_bytes,
                            #    stat.rx_dropped, stat.tx_dropped)

                key = (dpid, port_no)
                value = (stat.tx_packets, stat.rx_packets, stat.tx_bytes, stat.rx_bytes, 
                        stat.rx_dropped, stat.tx_dropped, now_timestamp)

                # 保存端口统计数据
                self._save_stats(self.port_stats, key, value, 5)

                # 计算丢包率
                if key[0] in self.port_loss_stats and key[1] in self.port_loss_stats[key[0]]:
                    prev_rx_dropped, prev_tx_dropped = self.port_loss_stats[key[0]][key[1]]
                    prev_stats = self.port_stats[key][-2]
                    
                    # 计算这个周期内的变化量
                    rx_packets_delta = stat.rx_packets - prev_stats[1]
                    tx_packets_delta = stat.tx_packets - prev_stats[0]
                    rx_dropped_delta = stat.rx_dropped - prev_rx_dropped
                    tx_dropped_delta = stat.tx_dropped - prev_tx_dropped

                    # 分别计算接收和发送方向的丢包率
                    rx_loss_rate = 0.0
                    tx_loss_rate = 0.0

                    if rx_packets_delta + rx_dropped_delta > 0:
                        rx_loss_rate = float(rx_dropped_delta) / (rx_packets_delta + rx_dropped_delta)
                    if tx_packets_delta + tx_dropped_delta > 0:
                        tx_loss_rate = float(tx_dropped_delta) / (tx_packets_delta + tx_dropped_delta)

                    # 取两个方向中的最大值作为链路的丢包率
                    loss_rate = max(rx_loss_rate, tx_loss_rate)
                    
                    # 更新链路丢包率
                    self._update_link_loss_rate(dpid, port_no, loss_rate)

                # 保存当前的丢包计数
                self.port_loss_stats[key[0]][key[1]] = (stat.rx_dropped, stat.tx_dropped)

                # 计算带宽相关信息
                port_stats = self.port_stats[key]
                if len(port_stats) > 1:
                    curr_stat = port_stats[-1][2]
                    # self.logger.info("Current Stat: %s", curr_stat)
                    prev_stat = port_stats[-2][2]
                    # self.logger.info("Previous Stat: %s", prev_stat)
                    period = self._get_period(port_stats[-1][6], port_stats[-2][6])
                    # self.logger.info("Period: %s", period)
                    speed = self._cal_speed(curr_stat, prev_stat, period)
                    # self.logger.info("Speed: %s", speed)

                    # 输出 curr_stat、prev_stat、period 和 speed





                    self._save_freebandwidth(dpid, port_no, speed)

    """
        收集网络时延信息
    """

    def _detector(self):
        """
            Delay detecting functon.
            Send echo request and calculate link delay periodically
        """
        while True:
            self._send_echo_request()
            self.add_delay_info()
            hub.sleep(1)

    def _send_echo_request(self):
        """
            Seng echo request msg to datapath. 控制器发送Echo Request以探测链路延迟，交换机接收到请求后，发送 Echo Reply 来响应控制器的请求。
        """
        datapaths = list(self.dpid_to_switch.values())
        for datapath in datapaths:
            parser = datapath.ofproto_parser
            data_time = "%.12f" % time.time()  # data_time 表示的是控制器发送 Echo Request 消息的时刻
            byte_arr = bytearray(data_time.encode())  # 时间戳转换为字节数组 byte_arr，这是发送数据的一部分

            echo_req = parser.OFPEchoRequest(datapath, data=byte_arr)  # 这里的data 是控制器向交换机发送 Echo Request消息的时间戳
            datapath.send_msg(echo_req)

            # Important! Don't send echo request together, Because it will
            # generate a lot of echo reply almost in the same time.
            # which will generate a lot of delay of waiting in queue
            # when processing echo reply in echo_reply_handler.
            hub.sleep(0.5)

    def add_delay_info(self):  # 遍历所有内部链路，获取每条链路的时延信息(285,313行方法)，并将这些时延信息更新到 self.topo_inter_link 字典和 self.graph 图对象中
        """
            Create link delay data, and save it into graph object.
        """
        link_to_port = self.topo_inter_link
        for link in link_to_port.keys():
            (src_dpid, dst_dpid) = link
            try:
                delay = self._get_delay(src_dpid, dst_dpid)
                self.topo_inter_link[(src_dpid, dst_dpid)][2] = delay
                self.graph[src_dpid][dst_dpid]['delay'] = delay
            except:
                pass

        link_to_port = self.topo_access_link
        for link in link_to_port.keys():
            (local_dpid, remote_dpid) = link
            # topo_access_link的键格式: (local_dpid, remote_dpid)
            # 其中local_dpid是本域交换机，remote_dpid是其他域交换机
            try:
                # 调用_get_access_delay时，参数顺序是(本域交换机, 其他域交换机)
                delay = self._get_access_delay(local_dpid, remote_dpid)
                self.topo_access_link[(local_dpid, remote_dpid)][2] = delay
                self.graph[local_dpid][remote_dpid]['delay'] = delay
            except:
                pass

    def _get_delay(self, src, dst):
        """
            Get link delay.
                        Controller
                        |        |
        src echo latency|        |dst echo latency
                        |        |
                   SwitchA-------SwitchB

                    fwd_delay---> 指的是数据包从控制器到交换机A，再到交换机B，最后到控制器的总的时延
                        <----reply_delay
            delay = (forward delay + reply delay - src datapath's echo latency 解释有问题
        """
        try:
            # 检查lldp_delay字典中是否有该链路的延迟信息
            if (src, dst) not in self.lldp_delay:
                self.logger.debug("链路 (%s, %s) 没有LLDP延迟信息", src, dst)
                return float(0)
            
            fwd_delay = self.lldp_delay[(src, dst)][0]
            
            # 检查echo_latency字典中是否有源交换机的echo延迟
            if src not in self.echo_latency:
                self.logger.debug("交换机 %s 没有echo延迟信息", src)
                return float(0)
            
            src_latency = self.echo_latency[src]
            dst_latency = self.lldp_delay[(src, dst)][1]

            delay = fwd_delay - (src_latency + dst_latency) / 2
            # print(f"Calculating inter delay: fwd={fwd_delay:.12f}    src_lat={src_latency:.12f}  dst_lat={dst_latency:.12f}  delay={delay:.12f}")
            return max(delay, 0)
        except KeyError as e:
            self.logger.debug("计算延迟时缺少键: %s, src=%s, dst=%s", e, src, dst)
            return float(0)
        except Exception as e:
            self.logger.debug("计算延迟时发生异常: %s, src=%s, dst=%s", e, src, dst)
            return float(0)
    # 计算接入链路的延迟
    def _get_access_delay(self, src, dst):
        """
            Get link delay for inter-domain links.
            
            域间链路的情况：
            - src: 本域交换机（dst_dpid）
            - dst: 其他域交换机（src_dpid）
            
            topo_access_link存储格式: (dst_dpid, src_dpid) = [port, timestamp, delay, bw, loss]
            其中dst_dpid是本域交换机，src_dpid是其他域交换机
            
            lldp_delay存储格式: (dst_dpid, src_dpid) = [lldpdelay, echodelay]
            其中lldpdelay是LLDP包从src_dpid到dst_dpid的延迟
            echodelay是src_dpid（其他域交换机）的echo延迟
            
                   ControllerA                        ControllerB
                        |                                 |
        dst echo latency|                                 |src echo latency (其他域)
                        |                                 |
                   SwitchA (dst)----------------------SwitchB (src)
                                <----forward delay
        """
        try:
            # 注意：topo_access_link的键是(dst_dpid, src_dpid)，其中dst是本域，src是其他域
            # 但调用时传入的是(src, dst)，需要检查两个方向
            # 检查lldp_delay字典中是否有该链路的延迟信息
            # 尝试(src, dst)和(dst, src)两个方向
            if (src, dst) in self.lldp_delay:
                fwd_delay = self.lldp_delay[(src, dst)][0]
                src_echodelay = self.lldp_delay[(src, dst)][1]  # 源交换机（其他域）的echo延迟
            elif (dst, src) in self.lldp_delay:
                # 如果存储的是反向，需要调整
                fwd_delay = self.lldp_delay[(dst, src)][0]
                src_echodelay = self.lldp_delay[(dst, src)][1]
            else:
                self.logger.debug("接入链路 (%s, %s) 没有LLDP延迟信息", src, dst)
                return float('inf')
            
            # src是本域交换机，可以获取其echo延迟
            # dst是其他域交换机，echo延迟从lldp_delay中获取
            if src not in self.echo_latency:
                self.logger.debug("本域交换机 %s 没有echo延迟信息（接入链路）", src)
                return float('inf')
            
            src_latency = self.echo_latency[src]  # 本域交换机的echo延迟
            dst_latency = src_echodelay  # 其他域交换机的echo延迟（从LLDP包中获取）
            
            # 计算实际链路延迟
            # fwd_delay是LLDP包从其他域交换机到本域交换机的总延迟
            # 需要减去两个交换机的echo延迟
            delay = fwd_delay - (src_latency + dst_latency) / 2
            self.logger.debug("计算接入链路延迟: src=%s, dst=%s, fwd_delay=%.6f, src_lat=%.6f, dst_lat=%.6f, delay=%.6f",
                            src, dst, fwd_delay, src_latency, dst_latency, delay)
            return max(delay, 0)
        except KeyError as e:
            self.logger.debug("计算接入链路延迟时缺少键: %s, src=%s, dst=%s", e, src, dst)
            return float('inf')
        except Exception as e:
            self.logger.debug("计算接入链路延迟时发生异常: %s, src=%s, dst=%s", e, src, dst)
            return float('inf')

    def _save_lldp_delay(self, src=0, dst=0, lldpdelay=0, echodelay=0):
        self.lldp_delay[(src, dst)] = [lldpdelay, echodelay]
    
    def _send_lldp_report_to_server(self, src_dpid, src_port_no, dst_dpid, dst_inport,
                                    send_time, echodelay_src, receive_time):
        """
        将LLDP探测信息上报给根控制器，由根控制器统一计算链路时延。
        
        Args:
            src_dpid: 发送LLDP包的交换机ID
            src_port_no: 发送LLDP包的端口号
            dst_dpid: 接收LLDP包的交换机ID（本域）
            dst_inport: 接收端口
            send_time: LLDP包的发送时间戳（来自发送端PortData）
            echodelay_src: 发送端交换机的echo时延
            receive_time: 本域接收LLDP包的时间戳
        """
        if not self.is_connected:
            self.logger.warning("未连接到server_agent，无法上报LLDP信息")
            return
        
        dst_echo = self.echo_latency.get(dst_dpid, 0.0)
        report_msg = {
            "type": "lldp_report",
            "src_dpid": src_dpid,
            "src_port_no": src_port_no,
            "dst_dpid": dst_dpid,
            "dst_inport": dst_inport,
            "send_time": send_time,
            "receive_time": receive_time,
            "src_echo": echodelay_src,
            "dst_echo": dst_echo
        }
        self.logger.debug("上报LLDP信息给server_agent: %s", report_msg)
        self._send_to_server(report_msg)
    
    def _handle_portdata_query(self, query_msg):
        """
        处理来自其他控制器的PortData查询请求
        
        Args:
            query_msg: 查询消息，包含src_dpid和src_port_no
        """
        src_dpid = query_msg.get('src_dpid')
        src_port_no = query_msg.get('src_port_no')
        request_id = query_msg.get('request_id')
        
        self.logger.debug("收到PortData查询请求: src_dpid=%s, src_port_no=%s", src_dpid, src_port_no)
        
        # 从switches实例的ports中查找PortData
        timestamp = None
        echodelay = 0.0
        
        if self.switches is not None:
            for port_obj in self.switches.ports.keys():
                if src_dpid == port_obj.dpid and src_port_no == port_obj.port_no:
                    port_data = self.switches.ports[port_obj]
                    timestamp = port_data.timestamp
                    echodelay = getattr(port_data, 'echo_delay', 0.0)
                    break
        
        # 构建响应消息
        response_msg = {
            "type": "portdata_response",
            "request_id": request_id,
            "src_dpid": src_dpid,
            "src_port_no": src_port_no,
            "timestamp": timestamp,
            "echodelay": echodelay,
            "status": "ok" if timestamp is not None else "not_found"
        }
        
        self.logger.debug("发送PortData查询响应: timestamp=%s, echodelay=%s", timestamp, echodelay)
        self._send_to_server(response_msg)
    
    def _handle_portdata_response(self, response_msg):
        """
        处理PortData查询响应，更新lldp_delay
        
        Args:
            response_msg: 响应消息，包含timestamp和echodelay
        """
        request_id = response_msg.get('request_id')
        src_dpid = response_msg.get('src_dpid')
        src_port_no = response_msg.get('src_port_no')
        timestamp = response_msg.get('timestamp')
        echodelay = response_msg.get('echodelay', 0.0)
        status = response_msg.get('status')
        
        # 查找对应的查询请求
        query_key = None
        for key in self.pending_portdata_queries.keys():
            if str(key) == request_id:
                query_key = key
                break
        
        if query_key is None:
            self.logger.warning("收到未匹配的PortData响应: request_id=%s", request_id)
            return
        
        # 从待处理列表中移除
        query_data = self.pending_portdata_queries.pop(query_key, None)
        if query_data is None:
            self.logger.warning("查询数据不存在: request_id=%s", request_id)
            return
        
        lldp_receive_time, query_time = query_data
        dst_dpid = query_key[2]  # (src_dpid, src_port_no, dst_dpid)
        
        if status == "ok" and timestamp is not None:
            # 计算LLDP延迟
            # lldp_receive_time是收到LLDP包的时间
            # timestamp是发送LLDP包的时间（从其他控制器的PortData获取）
            # 直接计算：LLDP延迟 = 收到LLDP包的时间 - 发送LLDP包的时间
            lldpdelay = lldp_receive_time - timestamp
            
            # 更新延迟信息
            self._save_lldp_delay(src=dst_dpid, dst=src_dpid, lldpdelay=lldpdelay, echodelay=echodelay)
            self.logger.debug("收到PortData响应并更新延迟: src=%s, dst=%s, lldpdelay=%.6f, echodelay=%.6f, "
                            "lldp_receive_time=%.6f, timestamp=%.6f",
                            src_dpid, dst_dpid, lldpdelay, echodelay, lldp_receive_time, timestamp)
        else:
            self.logger.warning("PortData查询失败: src_dpid=%s, src_port_no=%s, status=%s", 
                              src_dpid, src_port_no, status)

    def _handle_lldp_delay_update(self, response_msg):
        """
        处理根控制器返回的LLDP延迟计算结果
        """
        status = response_msg.get('status', 'ok')
        if status != 'ok':
            message = response_msg.get('message')
            # send_time/receive_time 缺失在跨控制器场景中可短暂出现，降级为 debug 防止日志噪音。
            if message == "send_time or receive_time missing":
                self.logger.debug("LLDP延迟更新跳过: %s", message)
            else:
                self.logger.warning("LLDP延迟更新失败: %s", message)
            return

        src_dpid = response_msg.get('src_dpid')
        dst_dpid = response_msg.get('dst_dpid')
        lldp_delay = response_msg.get('fwd_delay', 0.0)
        src_echo = response_msg.get('src_echo', 0.0)
        dst_echo = response_msg.get('dst_echo', 0.0)
        calc_delay = response_msg.get('delay', 0.0)

        if src_dpid is None or dst_dpid is None:
            self.logger.warning("LLDP延迟更新缺少必要字段: %s", response_msg)
            return

        # 保存原始LLDP转发时延及发送端echo，用于后续计算
        self._save_lldp_delay(src=dst_dpid, dst=src_dpid, lldpdelay=lldp_delay, echodelay=src_echo)

        # 同时更新链路计算出的实际延迟
        try:
            if (dst_dpid, src_dpid) in self.topo_access_link:
                self.topo_access_link[(dst_dpid, src_dpid)][2] = calc_delay
                self.graph[dst_dpid][src_dpid]['delay'] = calc_delay
        except Exception:
            pass

        self.logger.debug("更新LLDP延迟: src=%s, dst=%s, fwd=%.6f, src_echo=%.6f, dst_echo=%.6f, delay=%.6f",
                          src_dpid, dst_dpid, lldp_delay, src_echo, dst_echo, calc_delay)

    # 处理 Echo 回复消息，计算链路延迟
    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def _echo_reply_handler(self, ev):
        """
            Handle the echo reply msg, and get the latency of link.
        """
        now_timestamp = time.time()
        try:
            latency = now_timestamp - eval(ev.msg.data)
            # 将交换机对应的echo时延写入字典保存起来
            self.echo_latency[ev.msg.datapath.id] = latency
            self.echo_timestamp[ev.msg.datapath.id] = now_timestamp  # 控制器收到交换机echo回复的时间戳
        except:
            print("echo reply error")
            return

    """
        获取交换机相关信息，包括ID编号、端口号、mac地址
    """

    def show(self):
        # if self.show_enable:
        while True:
            print("***********************")
            print("交换机列表", self.dpid_to_switch.keys())
            print("交换机端口地址对应列表",self.switch_mac_to_port)
            print("内部链路", self.topo_inter_link)
            print("主机链路", self.host_to_sw_port)
            self.arp_table.clear()
            print("外部链路",self.topo_access_link)
            print("图中的链路信息",self.graph.edges(data=True))
            

            print("\n")
            hub.sleep(2)

    # def switches_role_detection(self):  # 未被调用 确定每台交换机当前的角色（主控、从属等）
    #     for i in self.dpid_to_switch.keys():
    #         datapath = self.dpid_to_switch[i]  # datapath 中存放的是 值,是该 DPID 关联的交换机对象
    #         self.send_role_request(datapath, datapath.ofproto.OFPCR_ROLE_NOCHANGE, 0)

    def get_path(self, src, dst):
        """
        计算从源交换机到目标交换机的最短路径
        """
        # 如果源和目标是同一个交换机，直接返回包含该交换机的列表
        if src == dst:
            return [src]
            
        try:
            path = nx.shortest_path(self.graph, src, dst)  # dijkstra
            return path   # 如果找到路径，返回计算得到的路径列表（list）
        except:
            self.logger.error("【错误】无法找到从交换机 %s 到交换机 %s 的路径", src, dst)
            return []

    def get_port(self, dpid, port_no):  # 检查给定的交换机（通过其 DPID）是否包含指定的端口号
        if port_no in self.switch_mac_to_port[dpid].keys():
            return True
        return False

    def _mark_permanent_link_port(self, dpid, port_no):
        """将端口标记为永久链路口，并清理该端口误学习主机记录。"""
        if dpid is None or port_no is None:
            return
        self.permanent_link_ports.setdefault(dpid, set()).add(port_no)
        self._purge_host_records_on_link_port(dpid, port_no)

    def _apply_configured_external_link_ports(self, dpid=None):
        """Pre-mark configured physical attachment ports as non-host link ports."""
        for configured_dpid, ports in EXTERNAL_LINK_PORTS.items():
            if dpid is not None and configured_dpid != dpid:
                continue
            for port_no in ports:
                self._mark_permanent_link_port(configured_dpid, port_no)

    def is_configured_external_link_port(self, dpid, port):
        return port in EXTERNAL_LINK_PORTS.get(dpid, set())

    def remember_external_host_source(self, mac, ip):
        if not remember_external_host_source(self.external_host_sources, mac, ip):
            return
        removed = purge_virtual_host_records_for_source(
            self.host_to_sw_port, mac, ip, VIRTUAL_SWITCH_DPID_MAX
        )
        if removed:
            self.logger.info(
                "清理外部链路误学习主机: mac=%s ip=%s removed=%s",
                mac, ip, removed,
            )

    def is_external_host_source(self, mac, ip):
        return is_external_host_source(self.external_host_sources, mac, ip)

    def should_skip_external_host_learning(self, mac, ip, dpid):
        return should_skip_external_host_learning(
            self.external_host_sources,
            mac,
            ip,
            dpid,
            VIRTUAL_SWITCH_DPID_MAX,
        )

    def should_drop_external_arp(self, src_ip, dst_ip):
        return should_drop_external_arp(src_ip, dst_ip, EXTERNAL_ARP_ALLOWED_PREFIXES)

    # 验证一个交换机和端口的组合是否存在于网络拓扑中
    def is_link_port(self, dpid, port):  # 检查指定的端口是否是交换机的链路端口
        if port in self.permanent_link_ports.get(dpid, set()):
            return True
        for link in self.topo_inter_link.keys():
            if dpid == link[0] and port == self.topo_inter_link[link][0]:
                return True # 该端口是交换机之间的链接端口
        for link in self.topo_access_link.keys():
            if dpid == link[0] and port == self.topo_access_link[link][0]:
                return True # 该端口是接入端口
        return False

    def _get_loop_safe_arp_flood_ports(self, dpid, in_port):
        return get_loop_safe_arp_flood_ports(
            dpid=dpid,
            in_port=in_port,
            switch_mac_to_port=self.switch_mac_to_port,
            topo_inter_link=self.topo_inter_link,
            topo_access_link=self.topo_access_link,
            is_link_port_fn=self.is_link_port,
            get_port_from_link_fn=self.get_port_from_link,
        )

    def _arp_dedup_should_drop(self, dpid, src_mac, src_ip, dst_ip, opcode):
        """
        同一台交换机上，同一源/目的 IP、同一源 MAC、同一 ARP opcode 在 TTL 内只处理一次，其余丢弃。
        """
        now = time.time()
        ttl = self._arp_seen_ttl
        expired = [k for k, t in self._arp_seen.items() if now - t > ttl]
        for k in expired:
            self._arp_seen.pop(k, None)
        dedup_key = (dpid, src_mac, src_ip, dst_ip, opcode)
        if dedup_key in self._arp_seen:
            self.logger.debug(
                "[ARP] dedup drop: src_ip=%s, dst_ip=%s, src_mac=%s, dpid=%s, opcode=%s",
                src_ip, dst_ip, src_mac, dpid, opcode,
            )
            return True
        self._arp_seen[dedup_key] = now
        return False

    def log_packet_watch(self, packet_kind, dpid, in_port, src_mac, dst_mac,
                         src_ip=None, dst_ip=None, extra=None):
        """
        统一打印 ARP/IP 的 PacketIn 观测日志。
        可通过 self.packet_watch_log_enable 一键关闭。
        """
        if not self.packet_watch_log_enable:
            return
        self.logger.info(
            "%s kind=%s dpid=%s in_port=%s src_mac=%s dst_mac=%s src_ip=%s dst_ip=%s extra=%s",
            self.packet_watch_prefix,
            packet_kind,
            dpid,
            in_port,
            src_mac,
            dst_mac,
            src_ip if src_ip is not None else "-",
            dst_ip if dst_ip is not None else "-",
            extra if extra is not None else "-",
        )

    def is_packet_capture_mode(self):
        """是否开启抓包模式（强制 ARP/IP 每包 PacketIn）。"""
        return bool(self.packet_capture_mode_enable)

    def add_flow(self, datapath, priority, match, actions, proto=0, hard_timeout=None, idle_timeout=None, buffer_id=None):
        """
        向交换机下发流表
        Deliver the flow table to the switch
        """
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]  # OFPInstructionActions 是一个 OpenFlow 指令，用于定义流表条目匹配后应执行的动作
        # if proto == 6:
        #     hard_timeout = 5

        priority_int = int(priority)
        if idle_timeout is None:
            idle_timeout = ROUTE_FLOW_IDLE_TIMEOUT if priority_int > 0 else 0
        if hard_timeout is None:
            hard_timeout = ROUTE_FLOW_HARD_TIMEOUT if priority_int > 0 else 0
        idle_timeout = int(idle_timeout)
        hard_timeout = int(hard_timeout)

        flow_mod_flags = ofproto.OFPFF_SEND_FLOW_REM if priority_int > 0 else 0

        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, idle_timeout=idle_timeout,
                                    hard_timeout=hard_timeout, match=match,
                                    instructions=inst,
                                    flags=flow_mod_flags)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    idle_timeout=idle_timeout,
                                    hard_timeout=hard_timeout, match=match,
                                    instructions=inst,
                                    flags=flow_mod_flags)   # 创建一个 OFPFlowMod 消息
        datapath.send_msg(mod)
        if self._active_flow_tracking is not None:
            self._active_flow_tracking.append({
                'dpid': datapath.id,
                'priority': priority_int,
                'match': self._serialize_match_for_delete(match),
                'idle_timeout': int(idle_timeout),
                'hard_timeout': int(hard_timeout),
            })
    #将数据包发送到指定的输出端口。
    def send_packet_to_outport(self, datapath, msg, in_port, actions):
        """
        进行广播设置
        Setting up a broadcast
        """
        data = None
        if msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER:  # 如果数据包没有被交换机缓存，控制器需要使用 msg.data 来重新构建数据包并发送出去。
            data = msg.data  # msg包含接收到的数据包的信息，数据包的原始字节数据，存储在 msg.data

        out = datapath.ofproto_parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
                                                   actions=actions, data=data)
        datapath.send_msg(out)
    def get_switch_id_by_ip(self, ip_address):
        for switch_id in self.host_to_sw_port:
            for port in self.host_to_sw_port[switch_id]:
                for host in self.host_to_sw_port[switch_id][port]:
                    if host[1] == ip_address:
                        return switch_id
    
    def get_switch_port_by_ip(self, ip_address):
        for switch_id in self.host_to_sw_port:
            for port in self.host_to_sw_port[switch_id]:
                for host in self.host_to_sw_port[switch_id][port]:
                    if host[1] == ip_address:
                        return port
    
    def get_mac_by_ip(self, ip_address):
        for switch_id in self.host_to_sw_port:
            for port in self.host_to_sw_port[switch_id]:
                for host in self.host_to_sw_port[switch_id][port]:
                    if host[1] == ip_address:
                        return host[0]

    def _get_task_type_by_host_ports(self, sport, dport):
        """根据 TCP/UDP 源/目的端口所在区间映射业务类型（先目的端口，再源端口）。"""
        if sport is None or dport is None:
            return 'default'
        for lo, hi, task in HOST_PORT_TASK_RANGES:
            if lo <= dport <= hi:
                return task
        for lo, hi, task in HOST_PORT_TASK_RANGES:
            if lo <= sport <= hi:
                return task
        return 'default'

    def _ofp_match_ip_l4(self, parser, in_port, src_ip, dst_ip, l4_fwd):
        kwargs = {
            'eth_type': ether.ETH_TYPE_IP,
            'in_port': in_port,
            'ipv4_src': src_ip,
            'ipv4_dst': dst_ip,
        }
        if l4_fwd:
            kwargs.update(l4_fwd)
        return parser.OFPMatch(**kwargs)

    @staticmethod
    def _coerce_l4_match_dict(raw):
        """将根控回传的 l4_match（如 JSON 反序列化结果）转为 OFPMatch 可用的整型字段。"""
        if not raw or not isinstance(raw, dict):
            return None
        try:
            return {str(k): int(v) for k, v in raw.items()}
        except (TypeError, ValueError):
            return None

    def _get_policy_for_task(self, task_type):
        return TASK_POLICY_MAP.get(task_type, TASK_POLICY_MAP['default'])

    def _get_flow_priority_for_task(self, task_type):
        return TASK_PRIORITY_MAP.get(task_type, TASK_PRIORITY_MAP['default'])
    # 检查每个交换机的每个端口连接的主机IP地址，如果找到匹配的IP地址，则返回对应的交换机ID
    # def get_switch_id_by_ip(self, ip_address):
        # sw = self.host_to_sw_port.keys()
        # for switch_id in sw:
            # for port in self.host_to_sw_port[switch_id].keys():
                # if ip_address in self.host_to_sw_port[switch_id][port]:   # 指定的IP地址（主机的IP地址）是否与某个交换机的特定端口连接的主机相关联。
                    # return switch_id

    # def get_switch_port_by_ip(self, ip_address):  # 通过目的IP地址找到与之关联的交换机端口
        # sw = self.host_to_sw_port.keys()
        # for switch_id in sw:
            # for port in self.host_to_sw_port[switch_id].keys():
                # if ip_address in self.host_to_sw_port[switch_id][port]:
                    # return port

    # def get_mac_by_ip(self, ip_address):
        # sw = list(self.host_to_sw_port.keys())
        # for switch_id in sw:
            # for port in self.host_to_sw_port[switch_id].keys():
                # if ip_address in self.host_to_sw_port[switch_id][port]:
                    # return self.host_to_sw_port[switch_id][port][0]  # 根据给定的IP地址（主机的IP地址）查找并返回与该 IP 地址关联的主机的 MAC 地址

    def get_port_from_link(self, dpid, next_id):
        if (dpid, next_id) in self.topo_inter_link.keys():
            return self.topo_inter_link[(dpid, next_id)][0]  # 返回的是（当前交换机）和 next_id（下一个设备 ID）之间连接的第一个端口的信息。返回的端口属于第一个交换机
        if (dpid, next_id) in self.topo_access_link.keys():
            return self.topo_access_link[(dpid, next_id)][0]
            
    def install_flow_entry(self, path, src_ip, dst_ip, port=None, msg=None, task_type='default', l4_fwd=None):
        """
        install flow entry 在 OpenFlow 交换机的流表中添加一条流表项
        l4_fwd: TCP/UDP 时下发与分类一致的 L4 匹配（与 packet-in 解析结果相同）；非 L4 则为 None。
        """
        print("**********33333333install flow entry**********")
        print(f"找到路径: {path}")
        flow_priority = self._get_flow_priority_for_task(task_type)
        l4_rev = l4_reverse_for_match(l4_fwd)
        num = len(path)
        if num == 1:  # 当路径中只有一个交换机时的流表安装和数据包转发
            dpid = path[0]
            datapath = self.dpid_to_switch[dpid]
            in_port = port
            
            # 直接查找目标IP对应的端口和MAC地址
            dst_port = None
            dst_mac_addr = None
            
            for p in self.host_to_sw_port.get(dpid, {}):
                for host_info in self.host_to_sw_port[dpid][p]:
                    if host_info[1] == dst_ip:
                        dst_port = p
                        dst_mac_addr = host_info[0]
                        break
            # for p in self.host_to_sw_port.get(dpid, {}):
                # host_info = self.host_to_sw_port[dpid][p]
                # if host_info[1] == dst_ip:
                    # dst_port = p
                    # dst_mac_addr = host_info[0]
                    # break
            
            if not dst_port or not dst_mac_addr:
                return
            
            # 获取源主机的MAC地址
            src_mac_addr = None
            for p in self.host_to_sw_port.get(dpid, {}):
                for host_info in self.host_to_sw_port[dpid][p]:
                    if host_info[1] == src_ip:
                        src_mac_addr = host_info[0]
                        break
                if src_mac_addr:
                    break
            
            if not src_mac_addr:
                # src_mac_addr = "未知"  # 如果找不到源MAC，使用默认值
                self.logger.warning(f"未能找到源主机 {src_ip} 的MAC地址,跳过流表下发")
                return
                           
            # 创建正向流表
            parser = datapath.ofproto_parser
            actions = [parser.OFPActionSetField(eth_dst=dst_mac_addr),
                      parser.OFPActionOutput(dst_port)]
            match = self._ofp_match_ip_l4(parser, in_port, src_ip, dst_ip, l4_fwd)
            self.add_flow(datapath, flow_priority, match, actions)
            
            # 创建反向流表
            actions_reverse = [parser.OFPActionSetField(eth_dst=src_mac_addr),
                              parser.OFPActionOutput(in_port)]
            match_reverse = self._ofp_match_ip_l4(parser, dst_port, dst_ip, src_ip, l4_rev)
            self.add_flow(datapath, flow_priority, match_reverse, actions_reverse)
            
            # 发送当前数据包
            if msg:
                self.send_packet_to_outport(datapath, msg, in_port, actions)
                
            self.logger.info("【成功】单交换机流表安装完成: %s <-> %s", src_ip, dst_ip)
        elif num == 2:  # 特殊处理：直接路径，只有源交换机和目标交换机
            # 处理源交换机
            src_dpid = path[0]
            dst_dpid = path[1]
            
            # 检查源交换机和目标交换机之间是否有直接连接
            src_to_dst_port = self.get_port_from_link(src_dpid, dst_dpid)
            
            if not src_to_dst_port:
                return
                
            # 源交换机流表
            src_datapath = self.dpid_to_switch[src_dpid]
            src_in_port = port
            src_out_port = src_to_dst_port
            
            # 正向流表
            sp = src_datapath.ofproto_parser
            src_actions = [sp.OFPActionOutput(src_out_port)]
            src_match = self._ofp_match_ip_l4(sp, src_in_port, src_ip, dst_ip, l4_fwd)
            self.add_flow(src_datapath, flow_priority, src_match, src_actions)
            
            # 目标交换机流表
            dst_datapath = self.dpid_to_switch[dst_dpid]
            dst_in_port = self.get_port_from_link(dst_dpid, src_dpid)
            
            if not dst_in_port:
                return
                
            # 查找目标IP对应的端口和MAC地址
            dst_out_port = None
            dst_mac_addr = None
            # 查找目标主机
            for p in self.host_to_sw_port.get(dst_dpid, {}):
                for host_info in self.host_to_sw_port[dst_dpid][p]:
                    if host_info[1] == dst_ip:
                        dst_out_port = p
                        dst_mac_addr = host_info[0]
                        break
                if dst_out_port:
                    break
            
            if not dst_out_port or not dst_mac_addr:
                return
                
            # 正向流表
            dp = dst_datapath.ofproto_parser
            dst_actions = [dp.OFPActionSetField(eth_dst=dst_mac_addr),
                          dp.OFPActionOutput(dst_out_port)]
            dst_match = self._ofp_match_ip_l4(dp, dst_in_port, src_ip, dst_ip, l4_fwd)
            self.add_flow(dst_datapath, flow_priority, dst_match, dst_actions)
            
            # 查找源IP对应的MAC地址
            src_mac_addr = None
            for p in self.host_to_sw_port.get(src_dpid, {}):
                for host_info in self.host_to_sw_port[src_dpid][p]:
                    if host_info[1] == src_ip:
                        src_mac_addr = host_info[0]
                        break
                if src_mac_addr:
                    break
                    
            if not src_mac_addr:
                # src_mac_addr = "未知"  # 如果找不到源MAC，使用默认值
                self.logger.warning(f"未能找到源主机 {src_ip} 的MAC地址，跳过流表下发")
                return
                
            # 反向流表 - 目标交换机
            dst_actions_reverse = [dp.OFPActionOutput(dst_in_port)]
            dst_match_reverse = self._ofp_match_ip_l4(dp, dst_out_port, dst_ip, src_ip, l4_rev)
            self.add_flow(dst_datapath, flow_priority, dst_match_reverse, dst_actions_reverse)
            
            # 反向流表 - 源交换机
            src_actions_reverse = [sp.OFPActionSetField(eth_dst=src_mac_addr),
                                  sp.OFPActionOutput(src_in_port)]
            src_match_reverse = self._ofp_match_ip_l4(sp, src_out_port, dst_ip, src_ip, l4_rev)
            self.add_flow(src_datapath, flow_priority, src_match_reverse, src_actions_reverse)
            
            # 发送当前数据包
            if msg:
                self.send_packet_to_outport(src_datapath, msg, src_in_port, src_actions)
                
            self.logger.info("【成功】两交换机流表安装完成: %s <-> %s, 路径: %s", src_ip, dst_ip, path)
        else:
            # 从最后一个交换机开始，逆序安装流表
            for i in range(num - 1, -1, -1):
                dpid = path[i]  # 当前处理的交换机ID
                
                if dpid in self.dpid_to_switch.keys():
                    datapath = self.dpid_to_switch[dpid]  # 获取交换机的datapath对象
                    
                    if i == 0:  # 第一个交换机（源交换机）
                        next_id = path[i + 1]
                        in_port = port
                        out_port = self.get_port_from_link(dpid, next_id)
                        
                        if not out_port:
                            continue
                        
                        # 正向流表
                        parser = datapath.ofproto_parser
                        actions = [parser.OFPActionOutput(out_port)]
                        match = self._ofp_match_ip_l4(parser, in_port, src_ip, dst_ip, l4_fwd)
                        self.add_flow(datapath, flow_priority, match, actions)
                        
                        # 反向流表
                        actions_reverse = [parser.OFPActionOutput(in_port)]
                        match_reverse = self._ofp_match_ip_l4(parser, out_port, dst_ip, src_ip, l4_rev)
                        self.add_flow(datapath, flow_priority, match_reverse, actions_reverse)
                        
                        # 发送当前数据包
                        if msg:
                            self.send_packet_to_outport(datapath, msg, in_port, actions)
                        
                    elif i == num - 1:  # 最后一个交换机（目标交换机）
                        last_id = path[i - 1]
                        in_port = self.get_port_from_link(dpid, last_id)
                        
                        if not in_port:
                            continue
                        
                        # 查找目标IP对应的端口和MAC地址
                        dst_port = None
                        dst_mac_addr = None
                        
                        for p in self.host_to_sw_port.get(dpid, {}):
                            for host_info in self.host_to_sw_port[dpid][p]:
                                if host_info[1] == dst_ip:
                                    dst_port = p
                                    dst_mac_addr = host_info[0]
                                    break
                            if dst_port:
                                break
                        
                        if not dst_port or not dst_mac_addr:
                            continue
                        
                        # 正向流表
                        parser = datapath.ofproto_parser
                        actions = [parser.OFPActionSetField(eth_dst=dst_mac_addr),
                                  parser.OFPActionOutput(dst_port)]
                        match = self._ofp_match_ip_l4(parser, in_port, src_ip, dst_ip, l4_fwd)
                        self.add_flow(datapath, flow_priority, match, actions)
                        
                        # 反向流表
                        actions_reverse = [parser.OFPActionOutput(in_port)]
                        match_reverse = self._ofp_match_ip_l4(parser, dst_port, dst_ip, src_ip, l4_rev)
                        self.add_flow(datapath, flow_priority, match_reverse, actions_reverse)
            
            self.logger.info("【成功】多交换机流表安装完成: %s <-> %s, 路径: %s", src_ip, dst_ip, path)

    """
        收集网络拓扑信息（包括交换机、主机、链路等信息），并且构建本地网络拓扑结构图
    """

    def _add_switch_map(self, sw):
        dpid = sw.dp.id
        self.logger.info('Register datapath: %016x, the ip address is %s', dpid, sw.dp.address)
        self.switch_mac_to_port.setdefault(dpid, {})   # 如果 dpid 已经在字典中，则返回对应的值。如果不存在，则将 dpid 添加到字典中，并赋值为一个新的空字典 {}
        self.host_to_sw_port.setdefault(dpid, {})
        self.permanent_link_ports.setdefault(dpid, set())
        self._apply_configured_external_link_ports(dpid)
        self.mac_to_port.setdefault(dpid, {})
        if dpid not in self.dpid_to_switch:
            self.dpid_to_switch[dpid] = sw.dp
            self.dpid_to_switch_ip[dpid] = sw.dp.address
            for p in sw.ports:   # 遍历交换机的所有端口，并将每个端口的端口号与其对应的硬件地址（MAC 地址）关联起来
                self.switch_mac_to_port[dpid][p.port_no] = p.hw_addr

    def _delete_switch_map(self, sw):
        if sw.dp.id in self.dpid_to_switch:
            self.logger.info('Unregister datapath: %016x', sw.dp.id)
            try:
                self.host_to_sw_port.pop(sw.dp.id,0)  # 从字典中删除某个键，并返回该键对应条目的值，如果指定的键不存在，则返回该默认值（在此处是 0）
                self.switch_mac_to_port.pop(sw.dp.id,0)
                self.mac_to_port.pop(sw.dp.id,0)
                self.dpid_to_switch.pop(sw.dp.id,0)
                self.dpid_to_switch_ip.pop(sw.dp.id,0)
                self.echo_timestamp.pop(sw.dp.id,0)
                self.permanent_link_ports.pop(sw.dp.id, None)

            except Exception as e:
                print("An error occured:", e)
    
                return

    def _update_switch_map(self, sw):
        dpid = sw.dp.id
        if dpid not in self.dpid_to_switch:
            self.logger.info('register again for datapath: %016x', sw.dp.id)
            self.switch_mac_to_port.setdefault(dpid, {})
            self.host_to_sw_port.setdefault(dpid, {})
            self.permanent_link_ports.setdefault(dpid, set())
            self._apply_configured_external_link_ports(dpid)
            self.mac_to_port.setdefault(dpid, {})
            self.dpid_to_switch_ip[dpid] = sw.dp.address
            self.dpid_to_switch[dpid] = sw.dp
            self.echo_timestamp[dpid] = time.time()
            for p in sw.ports:
                self.switch_mac_to_port[dpid][p.port_no] = p.hw_addr

    def delete_switch(self, dpid):
        if dpid in self.dpid_to_switch:
            self.logger.info('connect time out  Unregister datapath: %016x', dpid)
            # try:
            #     self.host_to_sw_port.pop(dpid,0)
            #     self.switch_mac_to_port.pop(dpid,0)
            #     self.mac_to_port.pop(dpid,0)
            #     self.dpid_to_switch.pop(dpid,0)
            #     self.dpid_to_switch_ip.pop(dpid,0)
            # except:
            #     pass  当前的 except 语句没有指定异常类型，建议明确捕获特定异常，以提高代码的可读性和维护性
            datapath = self.dpid_to_switch[dpid]
            datapath.socket.close()  # 关闭与交换机之间的网络连接
            datapath.close()   # 释放与 datapath 相关的其他资源，执行必要的清理操作

    def _check_switch_state(self, echo_timestamp):  # 参数最后一次收到该交换机 Echo 回复的时间戳
        while True:
            check_switch_list = echo_timestamp  # echo_timestamp：键是交换机的DPID（Data Path ID），值是最后一次收到该交换机Echo回复的时间戳。
            curr_time = time.time()
            for dpid in list(check_switch_list.keys()):  # 遍历交换机的 DPID 是为了检查每个交换机的状态，尤其是判断它们是否在指定的时间内没有响应回声请求。将键的视图转换为一个列表
                if (curr_time - check_switch_list[dpid]) > 70:
                    self.logger.info("_check_switch_state方法中删除交换机: %016x", dpid)  # 添加打印信息
                    echo_timestamp.pop(dpid, 0)
                    hub.spawn(self.delete_switch, dpid)
            hub.sleep(5)

    def _remove_access_link_pair(self, u, v):
        """删除与无向交换机对 (u,v) 一致的域间 access 记录及对应有向边，避免与 topo_inter_link 重复。"""
        for key in list(self.topo_access_link.keys()):
            a, b = key[0], key[1]
            if (a == u and b == v) or (a == v and b == u):
                self.topo_access_link.pop(key, None)
                try:
                    self.graph.remove_edge(a, b)
                except nx.NetworkXError:
                    pass

    def _purge_host_records_on_link_port(self, dpid, port_no):
        """
        当某端口被确认是交换机链路端口时，清理该端口下可能误学习到的主机记录。
        解决“内部链路被误识别为主机链路”的残留状态问题。
        """
        if dpid not in self.host_to_sw_port:
            return
        if port_no not in self.host_to_sw_port.get(dpid, {}):
            return

        removed_hosts = self.host_to_sw_port[dpid].pop(port_no, [])
        # 清理 mac_to_port 中映射到该端口的条目
        if dpid in self.mac_to_port:
            for mac in list(self.mac_to_port[dpid].keys()):
                ports = self.mac_to_port[dpid][mac]
                if port_no in ports:
                    ports.discard(port_no)
                if not ports:
                    del self.mac_to_port[dpid][mac]
        # 清理 arp_table 中来自该端口的条目
        for key in list(self.arp_table.keys()):
            if key[0] == dpid and self.arp_table.get(key) == port_no:
                del self.arp_table[key]

        if removed_hosts:
            self.logger.warning(
                "端口被识别为链路口后清理误学习主机: dpid=%s, port=%s, hosts=%s",
                dpid, port_no, removed_hosts
            )

    def add_inter_link(self, link):   # 添加交换机之间的链路信息
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        src_port = link.src.port_no
        if (src_dpid, dst_dpid) not in self.topo_inter_link:
            self.topo_inter_link[(src_dpid, dst_dpid)] = [src_port, 0, 0, 0, 0]
            self._mark_permanent_link_port(src_dpid, src_port)
            self.graph.add_edge(src_dpid, dst_dpid)
            self._remove_access_link_pair(src_dpid, dst_dpid)

    def delete_inter_link(self, link):
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        if (src_dpid, dst_dpid) in self.topo_inter_link:
            del self.topo_inter_link[(src_dpid, dst_dpid)]
            self.graph.remove_edge(src_dpid, dst_dpid)

    def link_timeout_detection(self, access_link):  #  access_link，这是一个字典，通常用于存储链路信息，其中键是源和目标节点的元组，值是与链路相关的属性（例如时间戳）
        """
        用于链路超时检测，如果某条链路超过一定时间没有进行更新，就会判定该链路失效，从而删除该链路信息，同步更新对外端口信息
        """
        while True:
            link_lists = access_link
            now_timestamp = time.time()
            for (src, dst) in list(link_lists.keys()):
                if (now_timestamp - link_lists[(src, dst)][1]) > 70:  # 当前的时间戳与该链接的最后更新时间戳。
                    try:
                        self.logger.info("域间交换机链路超时，删除交换机链路: 从交换机 %s 到交换机 %s", src, dst)  # 添加打印信息
                        access_link.pop((src, dst))
                        self.graph.remove_edge(src, dst)
                    except:
                        pass
            hub.sleep(3)

    @set_ev_cls([event.EventSwitchEnter])
    def _switch_enter_handle(self, ev):
        switch = ev.switch
        self._add_switch_map(switch)  # 将新加入的交换机信息添加到应用内部的数据结构中

    @set_ev_cls([event.EventSwitchReconnected])
    def _switch_reconnected_handle(self, ev):
        print("reconnected the switch !!!")
        switch = ev.switch
        self._update_switch_map(switch)

    @set_ev_cls([event.EventSwitchLeave])
    def _switch_leave_handle(self, ev):
        switch = ev.switch
        self._delete_switch_map(switch)

    @set_ev_cls([event.EventLinkAdd])
    def add_link(self, ev):
        link = ev.link
        self.add_inter_link(link)
        self._notify_link_state("link_up", link.src.dpid, link.dst.dpid)

    @set_ev_cls([event.EventLinkDelete])
    def delete_link(self, ev):
        link = ev.link
        self.delete_inter_link(link)
        self._notify_link_state("link_down", link.src.dpid, link.dst.dpid)
        self._invalidate_sessions_on_link_failure(link.src.dpid, link.dst.dpid)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):  # 该方法未被调用,在交换机连接时获取其特征（如端口信息）???
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # 初始化一个新的匹配条件对象。
        match = parser.OFPMatch()  # OFPMatch 允许控制器指定要匹配的数据包头字段，例如源 IP 地址、目标 IP 地址、源端口、目标端口、协议类型等
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]  # 将数据包发送到控制器,控制器不应缓存数据包，而是立即处理它
        self.add_flow(datapath, 0, match, actions)

    # @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    # def _state_change_handler(self, ev):
    #     datapath = ev.datapath
    #     if ev.state == MAIN_DISPATCHER:
    #         if datapath.id not in self.dpid_to_switch:
    #             self.logger.info('Register datapath: %016x, the ip address is %s', datapath.id, datapath.address)
    #             self.dpid_to_switch.setdefault(datapath.id,None)
    #             self.switch_mac_to_port.setdefault(datapath.id,{})
    #             self.host_to_sw_port.setdefault(datapath.id,{})
    #             self.dpid_to_switch_ip.setdefault(datapath.id,{})
    #             self.mac_to_port.setdefault(datapath.id,{})
    #
    #             self.dpid_to_switch[datapath.id] = datapath
    #             self.dpid_to_switch_ip[datapath.id] = datapath.address
    #     elif ev.state == DEAD_DISPATCHER:
    #         if datapath.id in self.dpid_to_switch:
    #             self.logger.info('Unregister datapath: %016x', datapath.id)
    #             try:
    #
    #                 del self.host_to_sw_port[datapath.id]
    #                 del self.switch_mac_to_port[datapath.id]
    #                 del self.mac_to_port[datapath.id]
    #                 del self.dpid_to_switch_ip[datapath.id]
    #                 del self.dpid_to_switch[datapath.id]
    #                 print("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    #             except Exception as e:
    #                 print("An error occured:", e)
    #                 print("yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy")
    #                 return


    """
        对数据包进行处理
    """

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _lldp_packet_in_handle(self, ev):
        return handle_lldp_packet_in(self, ev)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _switch_packet_in_handle(self, ev):
        return handle_switch_packet_in(self, ev)
#_lldp_packet_in_handle 和 _host_arp_packet_in_handle 方法：分别处理LLDP和ARP数据包，用于发现链路和主机信息。
    # 发现主机，存主机信息（存host_to_sw_port里）
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)  # 记录主机的MAC地址、IP地址以及对应的输入端口，控制器可以逐步构建网络的主机信息数据库
    def _host_arp_packet_in_handle(self, ev):
        return handle_host_arp_packet_in(self, ev)

    def _check_host_migration(self, mac, ip, new_dpid, new_port):
        """
        检查主机是否已经迁移，如果是，则删除旧的链路信息
        
        Args:
            mac: 主机MAC地址
            ip: 主机IP地址
            new_dpid: 新的交换机ID
            new_port: 新的端口号
        """
        if self.is_link_port(new_dpid, new_port):
            return
        # 遍历所有交换机和端口，查找该主机的旧位置
        for sw_id in list(self.host_to_sw_port.keys()):
            for port in list(self.host_to_sw_port.get(sw_id, {}).keys()):
                # host_info = self.host_to_sw_port[sw_id].get(port)
                # 
                # 如果找到相同MAC地址的主机，但位置不同
                # if host_info and host_info[0] == mac:
                hosts = self.host_to_sw_port[sw_id][port]
                for h in list(hosts):  # 遍历端口下所有主机
                    if h[0] == mac and h[1] == ip:  #避免同一 MAC 在不同端口/交换机下出现多条不同 IP 的记录。
                    # 如果是同一个交换机的不同端口，或者不同交换机
                        if sw_id != new_dpid or port != new_port:
                            if self.host_migration_log_enable:
                                self.logger.info("主机迁移: MAC=%s, IP=%s 从交换机=%s,端口=%s 迁移到 交换机=%s,端口=%s",
                                            mac, ip, sw_id, port, new_dpid, new_port)
                        
                            hosts.remove(h)
                            # 如果该端口下没有主机了，删除端口
                            if not hosts:
                                del self.host_to_sw_port[sw_id][port]
                            
                        
                            # 如果交换机没有连接任何主机，清理该交换机的条目
                            if not self.host_to_sw_port[sw_id]:
                                del self.host_to_sw_port[sw_id]
                        
                             # 清理mac_to_port
                            if sw_id in self.mac_to_port and mac in self.mac_to_port[sw_id]:
                                self.mac_to_port[sw_id][mac].discard(port)
                                if not self.mac_to_port[sw_id][mac]:
                                    del self.mac_to_port[sw_id][mac]
                            
                            # 清理相关的ARP表条目
                            for key in list(self.arp_table.keys()):
                                if key[0] == sw_id and key[1] == mac:
                                    del self.arp_table[key]
                            return  # 找到并处理了迁移，退出函数

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)  # 处理接收到的 IP 数据包，通过解析并判断数据包的目的地，决定是直接安装流表项进行转发，还是请求路由信息
    def _host_ip_packet_in_handle(self, ev):
        return handle_host_ip_packet_in(self, ev)

    def _update_link_loss_rate(self, dpid, port_no, loss_rate):
        """
        更新链路的丢包率
        :param dpid: 交换机ID
        :param port_no: 端口号
        :param loss_rate: 丢包率
        """
        # 更新内部链路丢包率
        for link in self.topo_inter_link:
            if link[0] == dpid and self.topo_inter_link[link][0] == port_no:
                self.topo_inter_link[link][4] = loss_rate
                # 更新图中的丢包率信息
                if link[0] in self.graph and link[1] in self.graph[link[0]]:
                    self.graph[link[0]][link[1]]['loss_rate'] = loss_rate
                break

        # 更新接入链路丢包率
        for link in self.topo_access_link:
            if link[0] == dpid and self.topo_access_link[link][0] == port_no:
                self.topo_access_link[link][4] = loss_rate
                # 更新图中的丢包率信息
                if link[0] in self.graph and link[1] in self.graph[link[0]]:
                    self.graph[link[0]][link[1]]['loss_rate'] = loss_rate
                break

    def _connect_to_server(self):
        """连接到server_agent的方法"""
        while True:
            try:
                if not self.is_connected:
                    self.logger.info("尝试连接到server_agent...")
                    self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.server_socket.connect(self.server_addr)
                    self.is_connected = True
                    self.logger.info("成功连接到server_agent")
                    
                    if self._recv_greenlet is not None:
                        try:
                            hub.kill(self._recv_greenlet)
                        except Exception:
                            pass
                    self._recv_greenlet = hub.spawn(self._receive_from_server)
            except Exception as e:
                self.logger.error(f"连接server_agent失败: {e}")
                if self.server_socket:
                    self.server_socket.close()
                self.is_connected = False
            hub.sleep(SERVER_CONFIG['reconnect_interval'])

    def _send_topo_loop(self):
        """定期发送拓扑信息到server"""
        while True:
            if self.is_connected:
                try:
                    self.logger.info("准备发送拓扑信息到server_agent")
                    # 构建主机信息
                    host_info = []
                    for dpid, ports in self.host_to_sw_port.items():
                        for port, hosts in ports.items():
                            for host in hosts:
                                if host[1] == "0.0.0.0":
                                    continue
                                logger.info(f"交换机{dpid}端口{port}下主机: MAC={host[0]}, IP={host[1]}")
                                host_info.append({
                                    'dpid': dpid,
                                    'port': port,
                                    'mac': host[0],
                                    'ip': host[1]
                                })
                    # self.logger.info(f"主机信息: {host_info}")
                    # 合并域内链路和域间链路
                    link_info = []
                    # 域内链路
                    for link in self.topo_inter_link.keys():
                        link_info.append({
                            'src': link[0],
                            'dst': link[1],
                            'src_port': self.topo_inter_link[link][0],
                            'delay': self.topo_inter_link[link][2],
                            'bw': self.topo_inter_link[link][3],
                            'loss': self.topo_inter_link[link][4]
                        })
                    # 域间链路
                    for link in self.topo_access_link.keys():
                        link_info.append({
                            'src': link[0],
                            'dst': link[1],
                            'src_port': self.topo_access_link[link][0],
                            'delay': self.topo_access_link[link][2],
                            'bw': self.topo_access_link[link][3],
                            'loss': self.topo_access_link[link][4]
                        })

                    # 构建链路信息，
                    # link_info = [{'src': link[0], 
                                #  'dst': link[1], 
                                #  'src_port': self.topo_access_link[link][0],
                                #  'delay': self.topo_access_link[link][2],
                                #  'bw': self.topo_access_link[link][3],
                                #  'loss': self.topo_access_link[link][4]
                                # } for link in self.topo_access_link.keys()]
                    # link_info = [{'src': link[0], 
                                #  'dst': link[1], 
                                #  'src_port': self.topo_inter_link[link][0],
                                #  'delay': self.topo_inter_link[link][2],
                                #  'bw': self.topo_inter_link[link][3],
                                #  'loss': self.topo_inter_link[link][4]
                                # } for link in self.topo_inter_link.keys()]
                    self.logger.info(f"链路信息: {link_info}")

                    # 构建拓扑信息
                    switch_flow_tables = {}
                    for dpid in self.dpid_to_switch.keys():
                        switch_flow_tables[str(dpid)] = self.switch_flow_stats.get(dpid, [])
                    route_sessions_info = []
                    for sid, session in self.route_sessions.items():
                        raw_path = session.get('path', []) or []
                        switch_path = [n for n in raw_path if isinstance(n, int)]
                        route_sessions_info.append({
                            'session_id': sid,
                            'switch_path': switch_path,
                            'src_ip': session.get('src_ip'),
                            'dst_ip': session.get('dst_ip'),
                            'task_type': session.get('task_type', 'default'),
                            'route_policy': session.get('route_policy', 'shortest_path'),
                            'l4_match': session.get('l4_match'),
                            'path_source': session.get('path_source', 'unknown'),
                            'decision_source': session.get('decision_source', session.get('path_source', 'unknown')),
                            'model_used': session.get('model_used', False),
                            'fallback_reason': session.get('fallback_reason'),
                            'model_confidence': session.get('model_confidence'),
                            'drl_compute_time': session.get('drl_compute_time'),
                            'drl_shadow': session.get('drl_shadow'),
                            'created_at': session.get('created_at', 0),
                            'updated_at': session.get('updated_at', session.get('created_at', 0)),
                        })

                    topo_msg = {
                        "type": "topo",
                        "switches": list(self.dpid_to_switch.keys()),
                        "switch_flow_tables": switch_flow_tables,
                        "link": link_info,
                        "host": host_info,
                        "route_sessions": route_sessions_info,
                    }
                    self.logger.info("发送拓扑信息到server_agent")
                    self._send_to_server(topo_msg)
                except Exception as e:
                    self.logger.error(f"发送拓扑信息失败: {e}")
            hub.sleep(10)

    def _send_to_server(self, msg):
        """发送消息到server"""
        if self.is_connected:
            try:
                data = json.dumps(msg) + '\n'  # 添加换行符作为消息分隔符
                with self._send_lock:
                    self.server_socket.sendall(data.encode())
            except Exception as e:
                self.logger.error(f"发送失败: {e}")
                self.is_connected = False
                if self.server_socket:
                    self.server_socket.close()

    def _heartbeat_loop(self):
        """定期向根控制器发送心跳，保持连接活跃"""
        while True:
            try:
                if self.is_connected:
                    self._send_to_server({"type": "heartbeat"})
            except Exception as e:
                self.logger.error(f"发送心跳失败: {e}")
                self.is_connected = False
                if self.server_socket:
                    self.server_socket.close()
            finally:
                hub.sleep(2)

    def _receive_from_server(self):
        """接收server消息的循环"""
        buffer = ""  # 用于累积未完成的消息
        while self.is_connected:
            try:
                data = self.server_socket.recv(4096)
                if not data:
                    break
                
                # 将接收到的数据添加到缓冲区
                buffer += data.decode('utf-8')
                
                # 按换行符分割消息
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if line:  # 如果不是空行
                        try:
                            msg = json.loads(line)
                            self._handle_server_msg(msg)
                        except json.JSONDecodeError as json_err:
                            self.logger.error(f"JSON解析失败: {json_err}，接收到的数据: {line[:100]}")
                        except Exception as e:
                            self.logger.error(f"处理消息失败: {e}")
                            
            except Exception as e:
                self.logger.error(f"接收失败: {e}")
                break
        
        self.is_connected = False
        if self.server_socket:
            self.server_socket.close()

    def _handle_server_msg(self, msg):
        """处理从server收到的消息"""
        # self.logger.info(f"收到server消息: {msg}")
        if not isinstance(msg, dict):
            self.logger.error(f"收到非字典类型消息: {msg}")
            return
        
        msg_type = msg.get('type')
        
        # 处理PortData查询请求（来自其他控制器）
        if msg_type == 'portdata_query':
            self._handle_portdata_query(msg)
            return
        
        # 处理PortData查询响应（来自server_agent）
        if msg_type == 'portdata_response':
            self._handle_portdata_response(msg)
            return
        
        # 处理根控制器计算后的LLDP延迟
        if msg_type == 'lldp_delay_update':
            self._handle_lldp_delay_update(msg)
            return
        
        # 处理Web界面手动下发/删除流表
        if msg_type == 'manual_flow_mod':
            self._handle_manual_flow_mod(msg)
            return

        if msg_type == 'host_update':
            self._handle_remote_host_update(msg)
            return
        
        # 处理路径响应
        if msg.get('status') == 'ok' and 'path' in msg:
            path = msg['path']
            if path:
                self.logger.info(f"收到路径: {path}")
                # 获取源IP和目标IP
                src_ip = msg.get('src_ip')
                dst_ip = msg.get('dst_ip')
                switch_id = msg.get('switch_id')
                in_port = msg.get('in_port')
                
                # 处理完整的路径信息
                l4_fwd = self._coerce_l4_match_dict(msg.get('l4_match'))
                self._begin_flow_tracking()
                try:
                    self._process_path(
                        path, src_ip, dst_ip,
                        task_type=msg.get('task_type', 'default'),
                        l4_fwd=l4_fwd,
                        hop_ports=msg.get('hop_ports') or {},
                        src_mac=msg.get('src_mac'),
                        dst_mac=msg.get('dst_mac'),
                        path_id=msg.get('path_id'),
                    )
                finally:
                    flow_records = self._end_flow_tracking()
                    self._record_route_session(path, flow_records, {
                        'src_ip': src_ip,
                        'dst_ip': dst_ip,
                        'task_type': msg.get('task_type', 'default'),
                        'route_policy': msg.get('route_policy', 'shortest_path'),
                        'l4_match': l4_fwd,
                        'switch_id': switch_id,
                        'in_port': in_port,
                        'path_source': msg.get('path_source', msg.get('decision_source', 'unknown')),
                        'decision_source': msg.get('decision_source', msg.get('path_source', 'unknown')),
                        'model_used': msg.get('model_used', False),
                        'fallback_reason': msg.get('fallback_reason'),
                        'model_confidence': msg.get('model_confidence'),
                        'drl_compute_time': msg.get('drl_compute_time'),
                        'drl_shadow': msg.get('drl_shadow'),
                    }, preferred_sid=msg.get('session_id'))
        elif msg.get('status') == 'error':
            self.logger.error(f"server_agent返回错误: {msg.get('message')}")

    def _handle_remote_host_update(self, msg):
        host = msg.get('host') or {}
        ip = host.get('ip')
        mac = host.get('mac')
        dpid = host.get('dpid')
        port = host.get('port')
        if not ip or not mac or dpid is None or port is None:
            return
        self.remote_hosts[ip] = {
            'mac': mac,
            'dpid': dpid,
            'port': port,
        }
        self.logger.info("[HostUpdate] remote host learned: ip=%s mac=%s dpid=%s port=%s",
                         ip, mac, dpid, port)

    def _handle_manual_flow_mod(self, msg):
        op = msg.get('op')
        switch_id = msg.get('switch_id')
        flow_id = msg.get('flow_id')
        priority = int(msg.get('priority', 10))
        match_raw = msg.get('match') or {}

        try:
            dpid = int(str(switch_id), 0)
        except (TypeError, ValueError):
            self.logger.error("手动流表指令 switch_id 非法: %s", switch_id)
            return

        datapath = self.dpid_to_switch.get(dpid)
        if datapath is None:
            self.logger.warning("手动流表目标交换机不在本控制器: dpid=%s", dpid)
            return

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        match_kwargs = {}

        for key, value in match_raw.items():
            if value is None or value == '':
                continue
            try:
                if key in ('eth_type', 'ip_proto'):
                    match_kwargs[str(key)] = int(value, 0) if isinstance(value, str) else int(value)
                elif key in ('in_port', 'tcp_src', 'tcp_dst', 'udp_src', 'udp_dst'):
                    match_kwargs[str(key)] = int(value)
                else:
                    match_kwargs[str(key)] = value
            except (TypeError, ValueError):
                self.logger.error("手动流表匹配字段无效: %s=%s", key, value)
                return

        match = parser.OFPMatch(**match_kwargs)

        if op == 'add':
            try:
                out_port = int(msg.get('out_port'))
                idle_timeout = int(msg.get('idle_timeout', 0))
                hard_timeout = int(msg.get('hard_timeout', 0))
            except (TypeError, ValueError):
                self.logger.error("手动流表 out_port/timeout 非法: %s", msg)
                return

            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(
                datapath=datapath,
                priority=priority,
                match=match,
                actions=actions,
                idle_timeout=idle_timeout,
                hard_timeout=hard_timeout
            )
            self.logger.info("手动下发流表成功: dpid=%s flow_id=%s match=%s out=%s pri=%s",
                             dpid, flow_id, match_kwargs, out_port, priority)
            return

        if op == 'delete':
            mod = parser.OFPFlowMod(
                datapath=datapath,
                command=ofproto.OFPFC_DELETE_STRICT,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                priority=priority,
                match=match
            )
            datapath.send_msg(mod)
            self.logger.info("手动删除流表成功: dpid=%s flow_id=%s match=%s pri=%s",
                             dpid, flow_id, match_kwargs, priority)
            return

        self.logger.warning("未知手动流表操作: %s", op)

    # def _process_path(self, path, src_ip, dst_ip):
        # """处理路径信息"""
        # print("**********1111111_process_path***************")
        # 找到本controller负责的交换机段
        # path结构: [host1_mac, sw1, sw2, ..., swN, host2_mac]
        # 找到本域内连续交换机片段
        # start_idx = -1
        # end_idx = -1
        
        # for i in range(1, len(path)-1): #因为路径的第一个和最后一个元素是主机地址（不是交换机）
            # dpid = path[i]
            # if dpid in self.dpid_to_switch:
                # if start_idx == -1:
                    # start_idx = i
                # end_idx = i
            # elif start_idx != -1:
                # 发现一段连续的本域交换机，下发流表
                # self._install_path_segment(path, start_idx, end_idx, src_ip, dst_ip)
                # 重置索引，寻找下一段
                # start_idx = -1
        
        # 理最后一段连续的本控制器交换机段
        # if start_idx != -1:
            # self._install_path_segment(path, start_idx, end_idx, src_ip, dst_ip)
        
    # def _process_path(self, path, src_ip, dst_ip):
    #     # path: [host1_ip, sw1, sw2, ..., swN, host2_ip]
    #     # 找到本控制器负责的交换机在路径中的索引
    #     for i in range(1, len(path) - 1):
    #         dpid = path[i]
    #         if dpid in self.dpid_to_switch:
    #             # 推断 in_port
    #             if i == 1:
    #                 in_port = self.get_switch_port_by_ip(src_ip)
    #             else:
    #                 prev_dpid = path[i - 1]
    #                 in_port = self.get_port_from_link(dpid, prev_dpid)
    #             # 只对本交换机下发流表
    #             self.install_flow_entry([dpid], src_ip, dst_ip, in_port)
    #             break  # 只处理一个交换机，直接退出
    
    def _process_path(self, path, src_ip, dst_ip, msg=None, task_type='default', l4_fwd=None,
                      hop_ports=None, src_mac=None, dst_mac=None, path_id=None):
        """
        处理server_agent返回的全局路径，为本控制器负责的交换机下发正向和反向流表
        path: [host1_ip, sw1, sw2, ..., swN, host2_ip]
        """
        hop_ports = hop_ports or {}
        flow_priority = self._get_flow_priority_for_task(task_type)
        l4_rev = l4_reverse_for_match(l4_fwd)
        src_mac_addr = self.get_mac_by_ip(src_ip) or src_mac
        local_switches_in_path = 0
        skipped_switches = 0
        barrier_datapaths = {}
        first_hop_datapath = None
        first_hop_in_port = None
        first_hop_actions = None
        for i in range(1, len(path) - 1):
            dpid = path[i]
            if dpid in self.dpid_to_switch:
                local_switches_in_path += 1
                datapath = self.dpid_to_switch[dpid]
                p = datapath.ofproto_parser
                # 推断 in_port
                if i == 1:
                    in_port = self.get_switch_port_by_ip(src_ip)
                    src_mac_addr = self.get_mac_by_ip(src_ip) or src_mac
                else:
                    prev_dpid = path[i - 1]
                    in_port = self.get_port_from_link(dpid, prev_dpid)
                    if in_port is None:
                        in_port = hop_ports.get(f"{prev_dpid}->{dpid}")
                # 推断 out_port
                if i == len(path) - 2:
                    # 最后一跳，出端口是目标主机端口
                    out_port = self.get_switch_port_by_ip(dst_ip)
                    dst_mac_addr = self.get_mac_by_ip(dst_ip) or dst_mac
                    if in_port is None or out_port is None or dst_mac_addr is None:
                        skipped_switches += 1
                        self.logger.error("[Path] skip dpid=%s missing in/out/mac: in=%s out=%s dst_mac=%s",
                                          dpid, in_port, out_port, dst_mac_addr)
                        continue
                    
                    # 正向流表
                    actions = [
                        p.OFPActionSetField(eth_dst=dst_mac_addr),
                        p.OFPActionOutput(out_port)
                    ]
                    match = self._ofp_match_ip_l4(p, in_port, src_ip, dst_ip, l4_fwd)
                    self.add_flow(datapath, flow_priority, match, actions)
                    # 反向流表
                    actions_reverse = [
                        p.OFPActionSetField(eth_dst=src_mac_addr),#这个地方是5.19加的
                        p.OFPActionOutput(in_port)
                    ]
                    match_reverse = self._ofp_match_ip_l4(p, out_port, dst_ip, src_ip, l4_rev)
                    self.add_flow(datapath, flow_priority, match_reverse, actions_reverse)
                    barrier_datapaths[dpid] = datapath
                else:
                    # 中间节点，出端口是到下一个交换机的端口
                    next_dpid = path[i + 1]
                    out_port = self.get_port_from_link(dpid, next_dpid)
                    if out_port is None:
                        out_port = hop_ports.get(f"{dpid}->{next_dpid}")
                    if in_port is None or out_port is None:
                        skipped_switches += 1
                        self.logger.error("[Path] skip dpid=%s missing in/out: in=%s out=%s",
                                          dpid, in_port, out_port)
                        continue
                    # 正向流表
                    actions = [
                        p.OFPActionOutput(out_port)
                    ]
                    match = self._ofp_match_ip_l4(p, in_port, src_ip, dst_ip, l4_fwd)
                    self.add_flow(datapath, flow_priority, match, actions)
                    # 反向流表
                    if i == 1:
                        # 首跳反向需要改目标MAC
                        src_mac_addr = self.get_mac_by_ip(src_ip) or src_mac
                        if src_mac_addr is None:
                            skipped_switches += 1
                            self.logger.error("[Path] skip reverse mac for dpid=%s src=%s", dpid, src_ip)
                            continue
                        actions_reverse = [
                            p.OFPActionSetField(eth_dst=src_mac_addr),
                            p.OFPActionOutput(in_port)
                        ]
                    else:
                        actions_reverse = [
                            p.OFPActionOutput(in_port)
                        ]
                    match_reverse = self._ofp_match_ip_l4(p, out_port, dst_ip, src_ip, l4_rev)
                    self.add_flow(datapath, flow_priority, match_reverse, actions_reverse)
                    barrier_datapaths[dpid] = datapath
                # 发送当前数据包
                if i == 1 and first_hop_datapath is None:
                    first_hop_datapath = datapath
                    first_hop_in_port = in_port
                    first_hop_actions = actions
                self.logger.info("【跨域流表安装】交换机=%s, in_port=%s, out_port=%s, src_ip=%s, dst_ip=%s, actions=%s",
                                dpid, in_port, out_port, src_ip, dst_ip, actions)
                # break  # 只处理本控制器的交换机

        barriers_ok = self._wait_for_flow_barriers(list(barrier_datapaths.values())) if barrier_datapaths else True
        if not barriers_ok:
            self.logger.warning(
                "[Path] continuing after flow barrier timeout: src=%s dst=%s local_switches=%s",
                src_ip, dst_ip, local_switches_in_path
            )

        if path_id:
            self._send_to_server({
                "type": "path_install_ack",
                "path_id": path_id,
                "src": src_ip,
                "dst": dst_ip,
                "installed": local_switches_in_path - skipped_switches,
                "skipped": skipped_switches,
                "barriers_ok": barriers_ok,
            })

        if msg and first_hop_datapath is not None and first_hop_actions is not None:
            self.send_packet_to_outport(first_hop_datapath, msg, first_hop_in_port, first_hop_actions)

        pending_key = (src_ip, dst_ip)
        self._path_requested.pop(pending_key, None)
        if pending_key in self._pending_path_packets:
            queue = self._pending_path_packets.pop(pending_key)
            if first_hop_datapath is not None and first_hop_actions is not None:
                for _dp, queued_msg, _queued_in_port in queue:
                    try:
                        self.send_packet_to_outport(
                            first_hop_datapath, queued_msg, first_hop_in_port, first_hop_actions)
                    except Exception as exc:
                        self.logger.error("[Path] forward queued packet failed: %s", exc)
                self.logger.info("[Path] forwarded %d queued packets for %s", len(queue), pending_key)
            else:
                self.logger.info("[Path] cleared %d queued packets for %s without local first hop",
                                 len(queue), pending_key)

    def _request_path(self, src_ip, dst_ip, dpid, in_port, msg, task_type='default',
                      route_policy='shortest_path', l4_fwd=None, session_id=None):
        """请求路径计算"""
        path_msg = {
            "type": "path_request",
            "src": src_ip,
            "dst": dst_ip,
            "switch_id": dpid,
            "in_port": in_port,
            "task_type": task_type,
            "route_policy": route_policy
        }
        if l4_fwd:
            path_msg['l4_match'] = l4_fwd
        if session_id is not None:
            path_msg['session_id'] = int(session_id)
        self.logger.info(
            "[PathRequest] send_to_root src=%s dst=%s switch=%s in_port=%s task=%s policy=%s session_id=%s",
            src_ip, dst_ip, dpid, in_port, task_type, route_policy, session_id
        )
        self._send_to_server(path_msg)
        # self.logger.info(f"已发送路径请求: {src_ip} -> {dst_ip}")
