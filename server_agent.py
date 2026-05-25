#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import logging
import json
import socket
import threading
import time
import signal
import networkx as nx
import traceback
from flask import Flask
try:
    from flask_cors import CORS
except ImportError:
    def CORS(*args, **kwargs):
        return None
from common_config import (
    CONTROLLER_IP,
    CONTROLLER_PORT,
    WEB_PORT,
    PATH_SERVICE_HOST,
    PATH_SERVICE_PORT,
    DRL_ROUTE_MODE,
    DRL_K_CANDIDATES,
)
from server_path_service import (
    build_k_shortest_candidates,
    build_hop_ports,
    build_topo_edges_for_path_service,
    handle_path_request_with_policy,
    validate_switch_path,
)
from web_api import register_web_api_routes
from web_ui_html import get_web_ui_html
from server_message_handlers import (
    process_message as process_message_handler,
    heartbeat_check_loop as heartbeat_check_loop_handler,
    cleanup_disconnected_client as cleanup_disconnected_client_handler,
)

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),  # 输出到控制台
        logging.FileHandler("./server.log", mode='w', encoding='utf-8')  # 输出到文件,w模式覆盖原有日志, a模式追加
    ]
)
logger = logging.getLogger("server_agent")

# 创建 Flask 应用
app = Flask(__name__)

# 启用 CORS，允许所有来源
CORS(app, resources={r"/api/*": {"origins": "*"}})

# 全局server_agent实例引用（在main()中初始化）
server_agent = None

server_agent = None

def _get_server_agent():
    return server_agent


register_web_api_routes(app, _get_server_agent)

VALID_DRL_ROUTE_MODES = ("spf", "shadow", "hybrid", "drl")


def parse_route_mode_arg(argv=None):
    parser = argparse.ArgumentParser(
        description="Start server_agent with an optional DRL route mode."
    )
    parser.add_argument(
        "route_mode",
        nargs="?",
        default=DRL_ROUTE_MODE,
        type=str.lower,
        choices=VALID_DRL_ROUTE_MODES,
        help="Routing mode: spf, shadow, hybrid, or drl.",
    )
    return parser.parse_args(argv).route_mode

# ==================== ServerAgent类定义 ====================

class ServerAgent:
    """服务器代理，处理客户端连接和消息"""
    def __init__(self, ip, port, route_mode=None):
        self.ip = ip
        self.port = port
        self.route_mode = route_mode or DRL_ROUTE_MODE
        self.sock = None
        self.is_running = False
        self.clients = {}  # {client_addr: (socket, thread)}
        self.client_last_heartbeat = {}  # {client_addr: last_heartbeat_timestamp}
        self.client_lock = threading.Lock()  # 用于保护clients字典的线程锁
        
        # 心跳检测配置
        self.heartbeat_interval = 2  # 心跳检测间隔（秒）
        self.heartbeat_timeout = 6   # 3 个发送周期内未收到消息判定断联
        
        # 存储所有控制器的拓扑信息
        # 键使用(ip, port)元组以区分相同IP但不同端口的控制器
        self.topo = {}  # {(controller_ip, port): link_info}
        self.host = {}  # {(controller_ip, port): host_info}
        self.controller_to_switches = {}  # {(controller_ip, port): [switch_ids]}
        self.controller_route_sessions = {}  # {(controller_ip, port): [route_session, ...]}
        # 交换机真实流表缓存（由各控制器周期上报）
        self.switch_flow_tables = {}  # {switch_id: [flow_entry, ...]}
        
        # 用于记录PortData查询请求的发起者
        # key: request_id, value: (请求控制器地址, 查询时间)
        self.portdata_query_requests = {}  # {request_id: (requester_addr, query_time)}
        
        # 用于路径计算的图
        self.G = nx.DiGraph()
        self.path_service_sock = None
        self.path_service_host = PATH_SERVICE_HOST
        self.path_service_port = PATH_SERVICE_PORT
        self.path_service_lock = threading.Lock()
        self.pending_path_installs = {}
        self.path_install_cond = threading.Condition()
        self.link_down_set = {}
        self.LINK_DOWN_TTL = 30
        self._connect_path_service()
        
        # 启动定时打印线程（使用单独的线程而不是hub）
        self.print_thread = threading.Thread(target=self.print_topo_info_loop)
        self.print_thread.daemon = True
        self.print_thread.start()

        # 启动心跳检测线程
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_check_loop)
        self.heartbeat_thread.daemon = True
        self.heartbeat_thread.start()
        
        logger.info("初始化完成，定时打印线程已启动，心跳检测线程已启动")

    def _get_web_ui_html(self):
        """生成Web可视化界面的HTML页面"""
        return get_web_ui_html()

    @staticmethod
    def _normalize_switch_id(raw):
        if raw is None:
            return None
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw), 0)
        except (TypeError, ValueError):
            return raw

    def _find_controller_for_switch(self, switch_id):
        for controller_key, switches in self.controller_to_switches.items():
            if switch_id in switches:
                return controller_key
        return None

    def _set_switch_flow_table(self, switch_id, flow_table):
        sid = self._normalize_switch_id(switch_id)
        self.switch_flow_tables[sid] = list(flow_table or [])
        if sid in self.G.nodes:
            self.G.nodes[sid]['flow_table'] = list(self.switch_flow_tables[sid])

    def _get_switch_flow_table(self, switch_id):
        sid = self._normalize_switch_id(switch_id)
        return list(self.switch_flow_tables.get(sid, []))

    def add_manual_flow(self, payload):
        switch_id = self._normalize_switch_id(payload.get('switch_id'))
        out_port = payload.get('out_port')
        priority = payload.get('priority', 10)
        idle_timeout = payload.get('idle_timeout', 0)
        hard_timeout = payload.get('hard_timeout', 0)
        match = payload.get('match', {})

        controller = self._find_controller_for_switch(switch_id)
        if controller is None:
            return {'status': 'error', 'message': f'未找到管理交换机 {switch_id} 的控制器'}

        flow_id = int(time.time() * 1000)
        flow_entry = {
            'id': flow_id,
            'priority': priority,
            'match': json.dumps(match, ensure_ascii=False, sort_keys=True),
            'action': f'OUTPUT:{out_port}',
            'packets': 0,
            'manual': True,
            'match_dict': match,
            'out_port': out_port,
            'idle_timeout': idle_timeout,
            'hard_timeout': hard_timeout,
        }

        flow_table = self._get_switch_flow_table(switch_id)
        flow_table.append(flow_entry)
        self._set_switch_flow_table(switch_id, flow_table)

        msg = {
            'type': 'manual_flow_mod',
            'op': 'add',
            'switch_id': switch_id,
            'flow_id': flow_id,
            'priority': priority,
            'match': match,
            'out_port': out_port,
            'idle_timeout': idle_timeout,
            'hard_timeout': hard_timeout,
        }
        self._send_to_controller(controller, msg)
        logger.info("手动流表下发请求: switch=%s, flow_id=%s, match=%s, out_port=%s",
                    switch_id, flow_id, match, out_port)
        return {'status': 'ok', 'flow': flow_entry}

    def delete_manual_flow(self, switch_id, flow_id):
        switch_id = self._normalize_switch_id(switch_id)
        if switch_id not in self.switch_flow_tables:
            return {'status': 'error', 'message': f'交换机 {switch_id} 不存在'}
        flow_table = self._get_switch_flow_table(switch_id)
        flow_idx = None
        flow_obj = None
        for idx, flow in enumerate(flow_table):
            if str(flow.get('id')) == str(flow_id):
                flow_idx = idx
                flow_obj = flow
                break

        if flow_idx is None:
            return {'status': 'error', 'message': f'未找到 flow_id={flow_id} 的规则'}

        del flow_table[flow_idx]
        self._set_switch_flow_table(switch_id, flow_table)

        controller = self._find_controller_for_switch(switch_id)
        if controller is not None:
            msg = {
                'type': 'manual_flow_mod',
                'op': 'delete',
                'switch_id': switch_id,
                'flow_id': flow_obj.get('id'),
                'priority': int(flow_obj.get('priority', 10)),
                'match': flow_obj.get('match_dict', {}),
            }
            self._send_to_controller(controller, msg)

        logger.info("手动删除流表请求: switch=%s, flow_id=%s", switch_id, flow_id)
        return {'status': 'ok', 'flow_id': flow_id}

    def handle_flow_removed(self, client_addr, message):
        switch_id = self._normalize_switch_id(message.get('switch_id'))
        priority = int(message.get('priority', 0))
        match = message.get('match') or {}

        old_table = self._get_switch_flow_table(switch_id)
        new_table = [
            flow for flow in old_table
            if not (
                int(flow.get('priority', 0)) == priority and
                flow.get('match_dict', {}) == match
            )
        ]
        self._set_switch_flow_table(switch_id, new_table)

        removed_sessions = set(message.get('removed_sessions') or [])
        route_sessions = self.controller_route_sessions.get(client_addr)
        if isinstance(route_sessions, list) and removed_sessions:
            self.controller_route_sessions[client_addr] = [
                session for session in route_sessions
                if session.get('session_id') not in removed_sessions
            ]

        logger.info(
            "flow_removed cached: controller=%s switch=%s priority=%s reason=%s removed=%s",
            client_addr, switch_id, priority, message.get('reason'), len(old_table) - len(new_table)
        )

    def start_web_server(self):
        """在单独的线程中启动 Flask 服务器"""
        def run_flask():
            try:
                # 禁用Flask的默认日志（避免过多输出）
                import logging
                log = logging.getLogger('werkzeug')
                log.setLevel(logging.WARNING)
                
                logger.info(f"Flask线程开始运行，准备绑定端口 {WEB_PORT}")
                print(f"Flask线程开始运行，准备绑定端口 {WEB_PORT}")
                
                app.run(host='0.0.0.0', port=WEB_PORT, debug=False, use_reloader=False, threaded=True)
            except Exception as e:
                logger.error(f"Flask Web服务器启动失败: {e}")
                logger.error(traceback.format_exc())
                print(f"Flask Web服务器启动失败: {e}")
                print(traceback.format_exc())
        
        web_thread = threading.Thread(target=run_flask, daemon=True)
        web_thread.start()
        
        # 等待一下让Flask有时间启动
        time.sleep(1)
        
        logger.info(f"Web 服务器线程已启动（端口 {WEB_PORT}）")
        logger.info(f"访问 http://localhost:{WEB_PORT} 查看拓扑可视化")
        print(f"Web 服务器线程已启动（端口 {WEB_PORT}）")
        print(f"访问 http://localhost:{WEB_PORT} 查看拓扑可视化")

    def start(self):
        """启动服务器"""
        try:
            # 启动 Web 服务器
            self.start_web_server()
            
            # 原有的 TCP 服务器启动代码
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.ip, self.port))
            self.sock.listen(5)
            self.is_running = True
            
            logger.info(f"服务器已启动，监听地址: {self.ip}:{self.port}")
            print(f"服务器已启动，监听地址: {self.ip}:{self.port}")
            
            while self.is_running:
                try:
                    client_sock, client_addr = self.sock.accept()
                    logger.info(f"接受连接: {client_addr}")
                    # print(f"接受连接: {client_addr}")
                    
                    # 为每个客户端创建新的线程
                    client_thread = threading.Thread(
                        target=self.handle_client,
                        args=(client_sock, client_addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                    
                    # 设置socket超时，用于心跳检测
                    client_sock.settimeout(self.heartbeat_timeout)
                    
                    # 保存线程信息和心跳时间戳
                    with self.client_lock:
                        self.clients[client_addr] = (client_sock, client_thread)
                        self.client_last_heartbeat[client_addr] = time.time()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.is_running:
                        logger.error(f"接受连接时出错: {e}")
                        print(f"接受连接时出错: {e}")
        except Exception as e:
            logger.error(f"启动服务器时出错: {e}")
            print(f"启动服务器时出错: {e}")
        finally:
            self.stop()
    
    def handle_client(self, client_sock, client_addr):
        """处理客户端连接"""
        buffer = ""  # 用于累积未完成的消息
        try:
            while self.is_running:
                try:
                    data = client_sock.recv(4096)
                    if not data:
                        logger.info(f"客户端 {client_addr} 关闭了连接")
                        print(f"客户端 {client_addr} 关闭了连接")
                        break
                    
                    # 将接收到的数据添加到缓冲区
                    buffer += data.decode('utf-8')
                    
                    # 按换行符分割消息
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if line:  # 如果不是空行
                            try:
                                # 处理单个完整的JSON消息
                                self.process_message(client_sock, client_addr, line.encode('utf-8'))
                            except Exception as e:
                                logger.error(f"处理消息时出错: {e}, 消息内容: {line[:100]}")
                                logger.error(traceback.format_exc())
                
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"接收数据时出错: {e}")
                    logger.error(traceback.format_exc())
                    print(f"接收数据时出错: {e}")
                    break
        except Exception as e:
            logger.error(f"处理客户端 {client_addr} 时出错: {e}")
            logger.error(traceback.format_exc())
            print(f"处理客户端 {client_addr} 时出错: {e}")
        finally:
            # 客户端连接关闭时的清理
            self.cleanup_disconnected_client(client_addr, reason="连接关闭")
    
    def process_message(self, client_sock, client_addr, data):
        return process_message_handler(self, client_sock, client_addr, data)

    def new_method(self, client_addr, message):
        logger.debug(f"从 {client_addr} 接收到消息: {message}")
    
    def heartbeat_check_loop(self):
        return heartbeat_check_loop_handler(self)
    
    def cleanup_disconnected_client(self, client_addr, reason="未知"):
        return cleanup_disconnected_client_handler(self, client_addr, reason=reason)
    
    def handle_topo_message(self, client_addr, message):
        """处理拓扑信息消息"""
        # 使用完整的client_addr（包含IP和端口）作为键
        controller_key = client_addr if isinstance(client_addr, tuple) else (client_addr, 0)
        logger.info(f"处理来自 {controller_key} 的拓扑信息")
        # print(f"处理来自 {controller_key} 的拓扑信息")
        
        # 保存交换机信息
        if 'switches' in message:
            normalized_switches = []
            for sw in message['switches'] or []:
                sw_id = self._normalize_switch_id(sw)
                if sw_id is not None:
                    normalized_switches.append(sw_id)
            self.controller_to_switches[controller_key] = normalized_switches
            logger.info(f"更新控制器 {controller_key} 的交换机: {normalized_switches}")
            # print(f"更新控制器 {controller_key} 的交换机: {message['switches']}")

        # 保存真实流表快照（可选字段）
        if 'switch_flow_tables' in message and isinstance(message['switch_flow_tables'], dict):
            for sw_key, flow_table in message['switch_flow_tables'].items():
                sw_id = self._normalize_switch_id(sw_key)
                self._set_switch_flow_table(sw_id, flow_table if isinstance(flow_table, list) else [])
        
        # 保存链路信息
        if 'link' in message:
            self.topo[controller_key] = message['link']
            logger.info(f"更新控制器 {controller_key} 的链路: {len(message['link'])} 条")
            # print(f"更新控制器 {controller_key} 的链路: {len(message['link'])} 条")
            
            # 打印链路详情
            for link in message['link']:
                logger.info(f"链路详情: {link}")
                # print(f"链路详情: {link}")
        
        # 保存主机信息
        if 'host' in message:
            self.host[controller_key] = message['host']
            logger.info(f"更新控制器 {controller_key} 的主机: {len(message['host'])} 个")
            # print(f"更新控制器 {controller_key} 的主机: {len(message['host'])} 个")
            
            # 打印主机详情
            for host in message['host']:
                logger.info(f"主机详情: {host}")
                # print(f"主机详情: {host}")

        # 保存控制器上报的路径会话（用于精简模式路由路径展示）
        route_sessions = message.get('route_sessions')
        if isinstance(route_sessions, list):
            self.controller_route_sessions[controller_key] = route_sessions
            logger.info(f"更新控制器 {controller_key} 的路径会话: {len(route_sessions)} 条")
        
        # 更新图
        self.update_graph()
        
        logger.info("拓扑信息处理完成")
        # print("拓扑信息处理完成")
    
    def handle_host_message(self, client_addr, message):
        """处理主机信息消息"""
        # 使用完整的client_addr（包含IP和端口）作为键
        controller_key = client_addr if isinstance(client_addr, tuple) else (client_addr, 0)
        if 'hosts' in message:
            self.host[controller_key] = message['hosts']
            logger.info(f"更新控制器 {controller_key} 的主机信息: {len(message['hosts'])} 个主机")
            # print(f"更新控制器 {controller_key} 的主机信息: {len(message['hosts'])} 个主机")
            
            # 更新图
            self.update_graph()
    
    def handle_portdata_query(self, client_addr, message):
        """
        处理PortData查询请求，路由到管理该交换机的控制器
        
        Args:
            client_addr: 请求控制器的地址
            message: 查询消息，包含src_dpid和src_port_no
        """
        src_dpid = message.get('src_dpid')
        request_id = message.get('request_id')
        
        logger.debug(f"收到PortData查询请求: src_dpid={src_dpid}, request_id={request_id}, 来自 {client_addr}")
        
        # 记录查询请求的发起者，用于后续路由响应
        self.portdata_query_requests[request_id] = (client_addr, time.time())
        
        # 查找管理该交换机的控制器
        target_controller = None
        for controller_key, switches in self.controller_to_switches.items():
            if src_dpid in switches:
                target_controller = controller_key
                break
        
        if target_controller is None:
            logger.warning(f"未找到管理交换机 {src_dpid} 的控制器")
            # 发送错误响应给请求的控制器
            error_response = {
                "type": "portdata_response",
                "request_id": request_id,
                "src_dpid": src_dpid,
                "status": "error",
                "message": f"Controller not found for switch {src_dpid}"
            }
            self._send_to_controller(client_addr, error_response)
            # 清理记录
            if request_id in self.portdata_query_requests:
                del self.portdata_query_requests[request_id]
            return
        
        # 如果目标控制器就是请求的控制器，直接返回（不应该发生，但处理一下）
        if target_controller == client_addr:
            logger.warning(f"PortData查询请求的交换机属于请求控制器本身: {src_dpid}")
            # 清理记录
            if request_id in self.portdata_query_requests:
                del self.portdata_query_requests[request_id]
            return
        
        # 转发查询请求到目标控制器
        logger.debug(f"转发PortData查询请求到控制器 {target_controller}")
        self._send_to_controller(target_controller, message)
    
    def handle_portdata_response(self, client_addr, message):
        """
        处理PortData查询响应，路由回请求的控制器
        
        Args:
            client_addr: 响应控制器的地址
            message: 响应消息，包含request_id
        """
        request_id = message.get('request_id')
        logger.debug(f"收到PortData查询响应: request_id={request_id}, 来自 {client_addr}")
        
        # 查找请求的控制器（从记录的查询请求中查找）
        if request_id in self.portdata_query_requests:
            requester_addr, query_time = self.portdata_query_requests[request_id]
            
            # 只将响应发送给发起查询的控制器
            logger.debug(f"转发PortData响应到请求控制器 {requester_addr}")
            self._send_to_controller(requester_addr, message)
            
            # 清理记录（响应已发送）
            del self.portdata_query_requests[request_id]
        else:
            logger.warning(f"未找到PortData查询请求记录: request_id={request_id}")
            # 如果找不到记录，可能是请求已超时或已被清理，忽略响应

    def handle_lldp_report(self, client_addr, message):
        """
        处理从控制器上报的LLDP信息，计算延迟并反馈相关控制器。
        """
        src_dpid = message.get('src_dpid')
        dst_dpid = message.get('dst_dpid')
        send_time = message.get('send_time')
        receive_time = message.get('receive_time')
        src_echo = float(message.get('src_echo', 0.0) or 0.0)
        dst_echo = float(message.get('dst_echo', 0.0) or 0.0)

        if src_dpid is None or dst_dpid is None:
            logger.warning("LLDP报告缺少交换机信息: %s", message)
            return

        if send_time is None or receive_time is None:
            error_resp = {
                "type": "lldp_delay_update",
                "status": "error",
                "message": "send_time or receive_time missing",
                "src_dpid": src_dpid,
                "dst_dpid": dst_dpid
            }
            self._send_to_controller(client_addr, error_resp)
            return

        try:
            fwd_delay = float(receive_time) - float(send_time)
            calc_delay = fwd_delay - (src_echo + dst_echo) / 2
            calc_delay = max(calc_delay, 0.0)
        except Exception as e:
            logger.error(f"计算LLDP延迟失败: {e}")
            error_resp = {
                "type": "lldp_delay_update",
                "status": "error",
                "message": f"calc error: {e}",
                "src_dpid": src_dpid,
                "dst_dpid": dst_dpid
            }
            self._send_to_controller(client_addr, error_resp)
            return

        resp = {
            "type": "lldp_delay_update",
            "status": "ok",
            "src_dpid": src_dpid,
            "dst_dpid": dst_dpid,
            "fwd_delay": fwd_delay,
            "src_echo": src_echo,
            "dst_echo": dst_echo,
            "delay": calc_delay
        }

        # 发送给上报控制器
        self._send_to_controller(client_addr, resp)

        # 同时发送给相关控制器（拥有src或dst交换机的控制器）
        targets = set()
        for controller_key, switches in self.controller_to_switches.items():
            if src_dpid in switches or dst_dpid in switches:
                targets.add(controller_key)

        for target in targets:
            if target != client_addr:
                self._send_to_controller(target, resp)

        logger.debug(f"LLDP延迟计算完成并分发: {resp}, targets={targets}")
    
    def _send_to_controller(self, controller_addr, message):
        """
        向指定控制器发送消息
        
        Args:
            controller_addr: 控制器地址（(ip, port)元组）
            message: 要发送的消息
        """
        with self.client_lock:
            if controller_addr in self.clients:
                sock, _ = self.clients[controller_addr]
                try:
                    data = json.dumps(message, ensure_ascii=False) + '\n'  # 添加换行符作为消息分隔符
                    sock.sendall(data.encode('utf-8'))
                    logger.debug(f"向控制器 {controller_addr} 发送消息: {message.get('type')}")
                except Exception as e:
                    logger.error(f"向控制器 {controller_addr} 发送消息失败: {e}")
            else:
                logger.warning(f"控制器 {controller_addr} 未连接")
    
    def update_graph(self):
        """更新网络图"""
        # 清空图
        self.G.clear()
        
        # 添加根控制器节点（用特殊标识）
        root_controller_id = "RootController"
        # 获取服务器IP地址（从配置中获取）
        root_ip = self.ip if hasattr(self, 'ip') else '0.0.0.0'
        self.G.add_node(root_controller_id, node_type='root_controller', ip=root_ip)
        
        # 收集所有控制器的标识（使用(ip, port)元组，不去重）
        controller_keys = set()
        
        # 从clients中获取（clients的键已经是(ip, port)元组）
        for client_addr in self.clients.keys():
            if isinstance(client_addr, tuple):
                controller_keys.add(client_addr)
            else:
                controller_keys.add((client_addr, 0))
        
        # 从topo中获取（现在键应该是(ip, port)元组）
        for controller_key in self.topo.keys():
            if isinstance(controller_key, tuple):
                controller_keys.add(controller_key)
            else:
                # 兼容旧数据：如果是字符串，转换为元组
                controller_keys.add((controller_key, 0))
        
        # 从controller_to_switches中获取
        for controller_key in self.controller_to_switches.keys():
            if isinstance(controller_key, tuple):
                controller_keys.add(controller_key)
            else:
                controller_keys.add((controller_key, 0))
        
        # 从host中获取
        for controller_key in self.host.keys():
            if isinstance(controller_key, tuple):
                controller_keys.add(controller_key)
            else:
                controller_keys.add((controller_key, 0))
        
        # 为每个控制器创建节点并连接到根控制器
        for controller_key in controller_keys:
            # 生成唯一的控制器ID（包含IP和端口）
            if isinstance(controller_key, tuple):
                ip, port = controller_key
                controller_id = f"Controller_{ip}_{port}"
            else:
                ip = controller_key
                port = 0
                controller_id = f"Controller_{ip}_{port}"
            
            self.G.add_node(controller_id, node_type='controller', ip=ip, port=port)
            # 从控制器连接到根控制器
            self.G.add_edge(root_controller_id, controller_id, 
                          edge_type='controller_connection', weight=1)
            logger.info(f"添加控制器节点: {controller_id} (IP: {ip}, Port: {port})")
        
        # 添加拓扑链路
        for controller_key, links in self.topo.items():
            # 生成控制器ID
            if isinstance(controller_key, tuple):
                ip, port = controller_key
                controller_id = f"Controller_{ip}_{port}"
            else:
                ip = controller_key
                port = 0
                controller_id = f"Controller_{ip}_{port}"
            
            # 确保控制器节点存在（应该已经存在了，但为了安全起见）
            if controller_id not in self.G:
                self.G.add_node(controller_id, node_type='controller', ip=ip, port=port)
                # 连接到根控制器
                if root_controller_id in self.G:
                    self.G.add_edge(root_controller_id, controller_id, 
                                  edge_type='controller_connection', weight=1)
            
            for link in links:
                # 适配controller.py发送的格式
                src = link.get('src')
                dst = link.get('dst')
                if src and dst:
                    # 先确保节点存在并设置正确的node_type（在添加边之前）
                    # 这样可以避免NetworkX自动创建没有属性的节点
                    if src not in self.G:
                        self.G.add_node(src, node_type='switch', flow_table=self._get_switch_flow_table(src))
                    else:
                        # 如果节点已存在但没有node_type，则更新它
                        if 'node_type' not in self.G.nodes[src] or self.G.nodes[src].get('node_type') != 'switch':
                            self.G.nodes[src]['node_type'] = 'switch'
                        self.G.nodes[src]['flow_table'] = self._get_switch_flow_table(src)
                    
                    if dst not in self.G:
                        self.G.add_node(dst, node_type='switch', flow_table=self._get_switch_flow_table(dst))
                    else:
                        # 如果节点已存在但没有node_type，则更新它
                        if 'node_type' not in self.G.nodes[dst] or self.G.nodes[dst].get('node_type') != 'switch':
                            self.G.nodes[dst]['node_type'] = 'switch'
                        self.G.nodes[dst]['flow_table'] = self._get_switch_flow_table(dst)
                    
                    # 添加边，可以设置权重等属性
                    delay = link.get('delay', 1)
                    bw = link.get('bw', 1)
                    loss = link.get('loss', 0)
                    
                    # 计算权重 (可以根据延迟、带宽和丢包率计算)
                    # 确保所有值都是有限的，避免产生inf或NaN
                    import math
                    if not math.isfinite(delay) or delay < 0:
                        delay = 1
                    if not math.isfinite(bw) or bw <= 0:
                        bw = 1
                    if not math.isfinite(loss) or loss < 0:
                        loss = 0
                    
                    weight = delay * (1 + loss) / bw
                    # 确保权重是有限的
                    if not math.isfinite(weight) or weight < 0:
                        weight = 1
                    
                    self.G.add_edge(src, dst, weight=weight, controller=controller_key,
                                   delay=delay, bw=bw, loss=loss,
                                   src_port=link.get('src_port'),
                                   edge_type='switch_link')
                    
                    # 添加交换机到控制器的连接（如果交换机属于该控制器）
                    if controller_id in self.G:
                        # 检查交换机是否属于该控制器
                        if controller_key in self.controller_to_switches:
                            if src in self.controller_to_switches[controller_key]:
                                if not self.G.has_edge(controller_id, src):
                                    self.G.add_edge(controller_id, src, 
                                                  edge_type='controller_switch', weight=0.5)
                            if dst in self.controller_to_switches[controller_key]:
                                if not self.G.has_edge(controller_id, dst):
                                    self.G.add_edge(controller_id, dst, 
                                                  edge_type='controller_switch', weight=0.5)
                    
                    logger.info(f"添加边: {src} -> {dst}, 权重: {weight}")
        
        # 添加交换机节点（即使没有链路）
        for controller_key, switches in self.controller_to_switches.items():
            # 生成控制器ID
            if isinstance(controller_key, tuple):
                ip, port = controller_key
                controller_id = f"Controller_{ip}_{port}"
            else:
                ip = controller_key
                port = 0
                controller_id = f"Controller_{ip}_{port}"
            
            # 确保控制器节点存在（应该已经存在了，但为了安全起见）
            if controller_id not in self.G:
                self.G.add_node(controller_id, node_type='controller', ip=ip, port=port)
                # 连接到根控制器
                if root_controller_id in self.G:
                    self.G.add_edge(root_controller_id, controller_id, 
                                  edge_type='controller_connection', weight=1)
            
            for switch_id in switches:
                if switch_id not in self.G:
                    self.G.add_node(
                        switch_id,
                        node_type='switch',
                        flow_table=self._get_switch_flow_table(switch_id)
                    )
                else:
                    # 如果节点已存在但没有node_type或node_type不正确，则更新它
                    if 'node_type' not in self.G.nodes[switch_id] or self.G.nodes[switch_id].get('node_type') != 'switch':
                        self.G.nodes[switch_id]['node_type'] = 'switch'
                    self.G.nodes[switch_id]['flow_table'] = self._get_switch_flow_table(switch_id)
                # 连接交换机到其控制器
                if not self.G.has_edge(controller_id, switch_id):
                    self.G.add_edge(controller_id, switch_id, 
                                  edge_type='controller_switch', weight=0.5)
        
        # 添加主机连接
        for controller_key, hosts in self.host.items():
            # 生成控制器ID
            if isinstance(controller_key, tuple):
                ip, port = controller_key
                controller_id = f"Controller_{ip}_{port}"
            else:
                ip = controller_key
                port = 0
                controller_id = f"Controller_{ip}_{port}"
            
            for host in hosts:
                # 适配controller.py发送的格式
                dpid = host.get('dpid')
                mac = host.get('mac')
                ip = host.get('ip')
                
                if dpid and ip:
                    # 确保交换机节点存在并设置正确的node_type
                    if dpid not in self.G:
                        self.G.add_node(dpid, node_type='switch')
                    else:
                        # 如果节点已存在但没有node_type或node_type不正确，则更新它
                        if 'node_type' not in self.G.nodes[dpid] or self.G.nodes[dpid].get('node_type') != 'switch':
                            self.G.nodes[dpid]['node_type'] = 'switch'
                    
                    # 添加主机节点并设置正确的node_type
                    if ip not in self.G:
                        self.G.add_node(ip, node_type='host', mac=mac)
                    else:
                        # 如果节点已存在但没有node_type或node_type不正确，则更新它
                        if 'node_type' not in self.G.nodes[ip] or self.G.nodes[ip].get('node_type') != 'host':
                            self.G.nodes[ip]['node_type'] = 'host'
                            if mac:
                                self.G.nodes[ip]['mac'] = mac
                    
                    # 添加主机到交换机的边
                    self.G.add_edge(ip, dpid, weight=1, controller=controller_key,
                                  edge_type='host_switch')
                    # 添加交换机到主机的边
                    self.G.add_edge(dpid, ip, weight=1, controller=controller_key,
                                  edge_type='host_switch')
                    
                    logger.info(f"添加主机连接: {mac} <-> {dpid}, IP: {ip}")
        
        logger.info(f"更新网络图完成: {len(self.G.nodes)} 个节点, {len(self.G.edges)} 条边")
        # print(f"更新网络图完成: {len(self.G.nodes)} 个节点, {len(self.G.edges)} 条边")
        print(f"**********G图节点: {list(self.G.nodes())}")
        print(f"**********G图边: {list(self.G.edges(data=True))}")
    
    def _lookup_host_mac(self, ip):
        for hosts in self.host.values():
            for host in hosts:
                if host.get('ip') == ip:
                    return host.get('mac')
        node_data = self.G.nodes.get(ip, {}) if ip in self.G else {}
        return node_data.get('mac')

    def _controllers_for_path(self, path):
        path_switches = {node for node in path if isinstance(node, int)}
        targets = set()
        with self.client_lock:
            connected = set(self.clients.keys())
        for controller_key, switches in self.controller_to_switches.items():
            if controller_key in connected and path_switches.intersection(set(switches or [])):
                targets.add(controller_key)
        return targets

    def handle_path_install_ack(self, client_addr, message):
        path_id = message.get('path_id')
        if not path_id:
            return
        with self.path_install_cond:
            pending = self.pending_path_installs.get(path_id)
            if not pending:
                return
            pending['acks'].discard(client_addr)
            self.path_install_cond.notify_all()

    def _cleanup_expired_portdata_queries(self):
        now = time.time()
        expired = [rid for rid, (_, qt) in self.portdata_query_requests.items() if now - qt > 60]
        for rid in expired:
            del self.portdata_query_requests[rid]
        if expired:
            logger.info("[PortData] TTL cleanup removed %d expired queries", len(expired))

    def _cleanup_expired_link_downs(self):
        now = time.time()
        expired = [(s, d) for (s, d), ts in self.link_down_set.items() if now - ts > self.LINK_DOWN_TTL]
        for edge in expired:
            self.link_down_set.pop(edge, None)
        if expired:
            logger.info("[LinkDown] TTL cleanup removed %d expired entries", len(expired))

    def handle_host_update(self, client_addr, message):
        controller_key = client_addr if isinstance(client_addr, tuple) else (client_addr, 0)
        host = message.get('host')
        if not host:
            return
        self.host.setdefault(controller_key, [])
        if not any(h.get('mac') == host.get('mac') or h.get('ip') == host.get('ip')
                   for h in self.host[controller_key]):
            self.host[controller_key].append(host)
            self.update_graph()
        relay_msg = {'type': 'host_update', 'host': host}
        for addr in list(self.clients.keys()):
            if addr != client_addr:
                self._send_to_controller(addr, relay_msg)

    def handle_link_down(self, client_addr, message):
        src = message.get('src')
        dst = message.get('dst')
        if src is None or dst is None:
            logger.warning("link_down missing src/dst: %s", message)
            return
        now = time.time()
        existing = self.link_down_set.get((src, dst))
        if existing is not None and now - existing < 30:
            return
        self._cleanup_expired_link_downs()
        self.link_down_set[(src, dst)] = now
        self.link_down_set[(dst, src)] = now
        self.update_graph()

    def handle_link_up(self, client_addr, message):
        src = message.get('src')
        dst = message.get('dst')
        if src is None or dst is None:
            return
        self.link_down_set.pop((src, dst), None)
        self.link_down_set.pop((dst, src), None)
        self.update_graph()

    def _connect_path_service(self):
        try:
            if self.path_service_sock:
                try:
                    self.path_service_sock.close()
                except Exception:
                    pass
            self.path_service_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.path_service_sock.settimeout(10)
            self.path_service_sock.connect((PATH_SERVICE_HOST, PATH_SERVICE_PORT))
            logger.info("[DRL] connected to path_service %s:%s", PATH_SERVICE_HOST, PATH_SERVICE_PORT)
        except Exception as exc:
            logger.warning("[DRL] path_service unavailable: %s; Dijkstra fallback will be used", exc)
            self.path_service_sock = None

    def _normalize_drl_decision(self, response, src_ip, dst_ip):
        if not response or not response.get('path'):
            return None
        full_path = [src_ip] + response['path'] + [dst_ip]
        valid, reason = validate_switch_path(self.G, full_path)
        if not valid:
            logger.warning("[DRL] invalid path from path_service (%s): %s", reason, full_path)
            return None
        return {
            'path': full_path,
            'decision_source': response.get('decision_source', 'path_service_unknown'),
            'model_used': bool(response.get('model_used', False)),
            'fallback_reason': response.get('fallback_reason'),
            'confidence': response.get('confidence'),
            'compute_time': response.get('compute_time'),
            'candidate_count': response.get('candidate_count'),
        }

    def _choose_final_path_response(self, message, drl_response, fallback_response, route_mode):
        if route_mode == 'spf':
            return fallback_response

        if route_mode == 'shadow':
            if fallback_response and fallback_response.get('status') == 'ok':
                fallback_response['path_source'] = 'shadow_fallback'
                fallback_response['decision_source'] = 'shadow_fallback'
                fallback_response['drl_shadow'] = {
                    'path': drl_response.get('path') if drl_response else None,
                    'decision_source': drl_response.get('decision_source') if drl_response else None,
                    'model_used': drl_response.get('model_used') if drl_response else False,
                    'fallback_reason': (
                        drl_response.get('fallback_reason')
                        if drl_response else 'no_drl_response'
                    ),
                    'model_confidence': drl_response.get('model_confidence') if drl_response else None,
                    'drl_compute_time': drl_response.get('drl_compute_time') if drl_response else None,
                }
            return fallback_response

        if route_mode == 'hybrid' and drl_response:
            return drl_response

        if route_mode == 'drl':
            return drl_response or {
                'status': 'error',
                'message': 'DRL route mode requested but no valid DRL path',
            }

        return fallback_response

    def _request_path_from_drl(self, message):
        src_ip = message.get('src')
        dst_ip = message.get('dst')
        route_policy = message.get('route_policy', 'shortest_path')
        try:
            src_dpid = None
            dst_dpid = None
            if src_ip in self.G:
                for node in self.G.neighbors(src_ip):
                    if isinstance(node, int):
                        src_dpid = node
                        break
            if dst_ip in self.G:
                for node in self.G.neighbors(dst_ip):
                    if isinstance(node, int):
                        dst_dpid = node
                        break
            if src_dpid is None or dst_dpid is None:
                return None

            request = {
                'type': 'path_request',
                'src_node': src_dpid,
                'dst_node': dst_dpid,
                'topo_edges': build_topo_edges_for_path_service(
                    self.G, self.link_down_set, route_policy),
                'candidates': build_k_shortest_candidates(
                    self.G,
                    src_ip,
                    dst_ip,
                    k=DRL_K_CANDIDATES,
                    link_down_set=self.link_down_set,
                    route_policy=route_policy,
                ),
                'route_mode': message.get('route_mode', self.route_mode),
                'route_policy': route_policy,
                'task_type': message.get('task_type', 'default'),
                'request_id': "%d-%d-%d" % (src_dpid, dst_dpid, int(time.time() * 1000)),
            }
            with self.path_service_lock:
                if self.path_service_sock is None:
                    self._connect_path_service()
                if self.path_service_sock is None:
                    return None
                try:
                    self.path_service_sock.sendall((json.dumps(request) + '\n').encode('utf-8'))
                    response_data = b''
                    while b'\n' not in response_data:
                        chunk = self.path_service_sock.recv(4096)
                        if not chunk:
                            raise ConnectionError("path_service disconnected")
                        response_data += chunk
                    line = response_data.split(b'\n', 1)[0]
                    response = json.loads(line.decode('utf-8'))
                except (socket.timeout, ConnectionError, json.JSONDecodeError) as exc:
                    logger.warning("[DRL] path_service request failed: %s", exc)
                    self._connect_path_service()
                    return None

            if response.get('status') == 'ok' and response.get('path'):
                return self._normalize_drl_decision(response, src_ip, dst_ip)
        except Exception as exc:
            logger.debug("[DRL] path_service call failed: %s", exc)
        return None

    def handle_path_request(self, message):
        """Handle controller path requests with DRL first and Dijkstra fallback."""
        src = message.get('src')
        dst = message.get('dst')
        if not src or not dst:
            return {'status': 'error', 'message': 'path request missing src or dst'}
        if src not in self.G or dst not in self.G:
            return {'status': 'error', 'message': 'src or dst not in graph'}

        route_mode = message.get('route_mode', self.route_mode)
        if route_mode not in {'spf', 'shadow', 'hybrid', 'drl'}:
            route_mode = self.route_mode

        drl_response = None
        if route_mode != 'spf':
            drl_decision = self._request_path_from_drl(message)
        else:
            drl_decision = None

        if drl_decision:
            drl_path = drl_decision['path']
            drl_response = {
                'status': 'ok',
                'path': drl_path,
                'src_ip': src,
                'dst_ip': dst,
                'src_mac': self._lookup_host_mac(src),
                'dst_mac': self._lookup_host_mac(dst),
                'switch_id': message.get('switch_id'),
                'in_port': message.get('in_port'),
                'task_type': message.get('task_type', 'default'),
                'route_policy': message.get('route_policy', 'shortest_path'),
                'path_source': drl_decision.get('decision_source', 'path_service_unknown'),
                'decision_source': drl_decision.get('decision_source', 'path_service_unknown'),
                'model_used': drl_decision.get('model_used', False),
                'fallback_reason': drl_decision.get('fallback_reason'),
                'model_confidence': drl_decision.get('confidence'),
                'drl_compute_time': drl_decision.get('compute_time'),
                'candidate_count': drl_decision.get('candidate_count'),
                'hop_ports': build_hop_ports(self.G, drl_path),
            }
            if 'l4_match' in message:
                drl_response['l4_match'] = message['l4_match']
            if 'session_id' in message:
                drl_response['session_id'] = message.get('session_id')

        fallback_response = None
        if route_mode in {'spf', 'shadow'} or drl_response is None:
            fallback_response = handle_path_request_with_policy(self.G, message, self.link_down_set)

        response = self._choose_final_path_response(
            message, drl_response, fallback_response, route_mode)
        if response and response.get('status') == 'ok':
            response['src_mac'] = self._lookup_host_mac(src)
            response['dst_mac'] = self._lookup_host_mac(dst)
        return response
    
    def stop(self):
        """停止服务器"""
        self.is_running = False
        
        # 关闭所有客户端连接
        for client_addr, (client_sock, _) in list(self.clients.items()):
            try:
                client_sock.close()
                logger.info(f"关闭客户端连接: {client_addr}")
                print(f"关闭客户端连接: {client_addr}")
            except:
                pass
        
        # 清空客户端列表
        self.clients.clear()
        
        # 关闭服务器套接字
        if self.sock:
            try:
                self.sock.close()
            except:
                pass

        if self.path_service_sock:
            try:
                self.path_service_sock.close()
            except:
                pass
        
        logger.info("服务器已停止")
        print("服务器已停止")

    def print_topo_info_loop(self):
        """定时打印拓扑信息"""
        logger.info("定时打印线程开始运行")
        # print("定时打印线程开始运行")
        
        while True:
            try:
                # 先打印一条日志确认线程在运行
                logger.info("定时打印线程正在运行...")
                # print("定时打印线程正在运行...")
                
                if self.topo or self.host or self.controller_to_switches:
                    logger.info("=" * 50)
                    logger.info("当前拓扑信息:")
                    
                    # 打印控制器信息
                    logger.info(f"已连接控制器数量: {len(self.clients)}")
                    for client_addr in self.clients:
                        logger.info(f"  - 控制器: {client_addr}")
                    
                    # 打印交换机信息
                    all_switches = set()
                    for controller_key, switches in self.controller_to_switches.items():
                        all_switches.update(switches)
                    logger.info(f"交换机总数: {len(all_switches)}")
                    for controller_key, switches in self.controller_to_switches.items():
                        controller_str = f"{controller_key[0]}:{controller_key[1]}" if isinstance(controller_key, tuple) else str(controller_key)
                        logger.info(f"  - 控制器 {controller_str} 管理的交换机: {switches}")
                    
                    # 打印链路信息
                    all_links = []
                    for controller_key, links in self.topo.items():
                        all_links.extend(links)
                    logger.info(f"链路总数: {len(all_links)}")
                    for controller_key, links in self.topo.items():
                        controller_str = f"{controller_key[0]}:{controller_key[1]}" if isinstance(controller_key, tuple) else str(controller_key)
                        logger.info(f"  - 控制器 {controller_str} 的链路:")
                        for link in links:
                            logger.info(f"    * {link}")
                    
                    # 打印主机信息
                    all_hosts = []
                    for controller_key, hosts in self.host.items():
                        all_hosts.extend(hosts)
                    logger.info(f"主机总数: {len(all_hosts)}")
                    for controller_key, hosts in self.host.items():
                        controller_str = f"{controller_key[0]}:{controller_key[1]}" if isinstance(controller_key, tuple) else str(controller_key)
                        logger.info(f"  - 控制器 {controller_str} 的主机:")
                        for host in hosts:
                            logger.info(f"    * {host}")
                    
                    # 打印图信息
                    logger.info(f"图节点数: {len(self.G.nodes)}, 边数: {len(self.G.edges)}")
                    logger.info("=" * 50)
                    
                    # 同时打印到控制台
                    print("=" * 50)
                    print("当前拓扑信息:")
                    print(f"已连接控制器数量: {len(self.clients)}")
                    print(f"交换机总数: {len(all_switches)}")
                    print(f"链路总数: {len(all_links)}")
                    print(f"主机总数: {len(all_hosts)}")
                    print(f"图节点数: {len(self.G.nodes)}, 边数: {len(self.G.edges)}")
                    print("=" * 50)
                else:
                    logger.info("当前没有拓扑信息")
                    # print("当前没有拓扑信息")
            except Exception as e:
                logger.error(f"打印拓扑信息时出错: {e}")
                print(f"打印拓扑信息时出错: {e}")
                traceback.print_exc()
            
            # 每10秒打印一次
            time.sleep(10)
    

def main(argv=None):
    """主函数"""
    global server_agent
    route_mode = parse_route_mode_arg(argv)
    
    # 创建ServerAgent实例并赋值给全局变量
    server_agent = ServerAgent(CONTROLLER_IP, CONTROLLER_PORT, route_mode=route_mode)
    logger.info("DRL route mode: %s", route_mode)
    
    # 注册信号处理器
    def signal_handler(sig, frame):
        print("\n接收到中断信号，正在关闭服务器...")
        server_agent.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动服务器
    server_agent.start()

if __name__ == "__main__":
    main()
