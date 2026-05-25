"""
path_service_v2.py - 修复版本的路径计算服务
主要修复：
  1. 通过环境变量强制 NetEnv 不连接 Mininet socket
  2. DRL 模型范围外的节点自动回退 Dijkstra（基于控制器传来的拓扑边）
  3. 保留独立 TCP 服务进程架构

使用方法：
    export SKIP_MININET_CONNECT=1
    python3 path_service_v2.py --topo Abi --port 8889 --model ./model/...
"""

import argparse
import json
import os
import random
import socket
import sys
import time
import threading

import numpy as np
try:
    import torch
except ImportError as exc:
    torch = None
    TORCH_IMPORT_ERROR = exc
else:
    TORCH_IMPORT_ERROR = None

try:
    from torch_geometric.data import Data
except ImportError as exc:
    Data = None
    TORCH_GEOMETRIC_IMPORT_ERROR = exc
else:
    TORCH_GEOMETRIC_IMPORT_ERROR = None

SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))

# 设置环境变量，告诉 NetEnv 不要连接 Mininet
os.environ['SKIP_MININET_CONNECT'] = '1'

# 确保可以导入本目录下的模块
sys.path.insert(0, SERVICE_DIR)


def _resolve_service_path(path):
    if not path:
        return os.path.join(SERVICE_DIR, "model", "Military_mininet")
    if os.path.isabs(path):
        return path
    return os.path.join(SERVICE_DIR, path)

NetEnv = None
Request = None
Policy = None

if torch is not None and Data is not None:
    # 尝试导入并打补丁
    try:
        from net_env import simenv

        # 保存原始的 NetEnv.__init__
        _original_netenv_init = simenv.NetEnv.__init__

        def _patched_netenv_init(self, args):
            """打补丁的 NetEnv.__init__，跳过 socket 连接"""
            original_socket = None
            try:
                import socket as socket_module
                original_socket = socket_module.socket

                class FakeSocket:
                    def connect(self, addr):
                        print("[PATCH] 跳过 socket.connect(%s)" % str(addr))
                        pass
                    def close(self):
                        pass
                    def send(self, data):
                        return len(data)
                    def recv(self, size):
                        return b''

                if os.getenv('SKIP_MININET_CONNECT') == '1':
                    socket_module.socket = lambda *a, **kw: FakeSocket()

                _original_netenv_init(self, args)
                socket_module.socket = original_socket

            except Exception as e:
                print("[PATCH] 警告：初始化时出现异常: %s" % e)
                import socket as socket_module
                if original_socket is not None:
                    socket_module.socket = original_socket
                raise

        simenv.NetEnv.__init__ = _patched_netenv_init
        print("[PATCH] NetEnv 补丁已应用")

    except Exception as e:
        print("[PATCH] 无法应用补丁: %s" % e)
        print("[PATCH] 将尝试正常加载...")

    try:
        from net_env.simenv import NetEnv, Request  # noqa: E402
        from a2c_ppo_acktr.model import Policy      # noqa: E402
    except Exception as exc:
        NET_ENV_IMPORT_ERROR = exc
else:
    NET_ENV_IMPORT_ERROR = TORCH_IMPORT_ERROR or TORCH_GEOMETRIC_IMPORT_ERROR


# ============================================================
#  Dijkstra 回退：基于控制器传来的拓扑边列表计算最短路径
# ============================================================
try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False
    print("[警告] networkx 未安装，Dijkstra 回退将使用简易 BFS")


def _normalize_topo_edge(edge):
    if isinstance(edge, dict):
        src = edge.get("src")
        dst = edge.get("dst")
        weight = edge.get("weight", 1)
    else:
        src = edge[0]
        dst = edge[1]
        weight = edge[2] if len(edge) > 2 else 1
    try:
        weight = float(weight)
        if not np.isfinite(weight) or weight < 0:
            weight = 1.0
    except Exception:
        weight = 1.0
    return int(src), int(dst), weight


def _dijkstra_on_edges(topo_edges, src, dst):
    """
    根据控制器传来的边列表构建有向图，用 Dijkstra 计算最短路径。
    topo_edges: [[src_dpid, dst_dpid], ...] （1-based DPID）
    src, dst: 1-based DPID
    返回: 1-based 路径列表，如 [1, 4, 7, 10]；失败返回 None
    """
    if not topo_edges:
        return None

    if HAS_NX:
        G = nx.DiGraph()
        for edge in topo_edges:
            u, v, weight = _normalize_topo_edge(edge)
            G.add_edge(u, v, weight=weight)
        if src not in G or dst not in G:
            return None
        try:
            path = nx.shortest_path(G, src, dst, weight="weight")
            return path
        except nx.NetworkXNoPath:
            return None
        except Exception:
            return None
    else:
        # 简易 BFS 回退（无 networkx 时）
        from collections import deque, defaultdict
        adj = defaultdict(list)
        for edge in topo_edges:
            u, v, _ = _normalize_topo_edge(edge)
            adj[u].append(v)
        visited = set()
        queue = deque()
        queue.append((src, [src]))
        visited.add(src)
        while queue:
            node, path = queue.popleft()
            if node == dst:
                return path
            for neighbor in adj[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return None


def _decision(decision_source, path, model_used=False, fallback_reason=None, confidence=None):
    return {
        "path": path,
        "decision_source": decision_source,
        "model_used": bool(model_used),
        "fallback_reason": fallback_reason,
        "confidence": confidence,
    }


class DRLPathService(object):
    """
    路径计算服务（独立 TCP 进程）
    - DRL 范围内的节点：使用训练好的 DRL 模型计算路径
    - DRL 范围外的节点：使用控制器传来的拓扑边做 Dijkstra 最短路径
    - 混合场景（路径跨越 DRL 范围内外）：整体走 Dijkstra
    """

    def __init__(self, topo_name="Military", port=8889, model_path=None):
        self.port = port
        self.topo_name = topo_name
        self.model_path = _resolve_service_path(model_path)

        print("[初始化] 拓扑: %s, 端口: %d" % (topo_name, port))

        if torch is None or Data is None or NetEnv is None or Policy is None:
            raise RuntimeError("DRL runtime dependencies unavailable: %s" % NET_ENV_IMPORT_ERROR)

        # 固定随机种子
        random.seed(1)
        np.random.seed(1)
        torch.manual_seed(1)

        # 初始化环境
        print("[初始化] 正在创建 NetEnv...")
        args = argparse.Namespace()
        args.use_mininet = False
        args.simu_port = 5000
        
        try:
            self.env = NetEnv(args)
            print("[初始化] NetEnv 创建成功")
        except ConnectionRefusedError as e:
            print("[初始化] 连接失败: %s" % e)
            raise

        print("[初始化] 正在加载拓扑信息...")
        (
            num_agent,
            num_node,
            observation_spaces,
            action_spaces,
            num_type,
            node_state_dim,
            agent_to_node,
            edge_indexs,
            adj_masks,
        ) = self.env.setup(topo_name)

        self.num_agent = num_agent
        self.num_node = num_node      # DRL 模型训练的节点数（0-based: 0 ~ num_node-1）
        self.node_state_dim = node_state_dim
        self.num_type = num_type
        self.agent_to_node = agent_to_node
        self.edge_indexs = edge_indexs
        self.adj_masks = adj_masks

        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.actor_critic = None
        self._last_model_action_used = False

        # 加载 DRL 模型
        if self.model_path:
            print("[模型] 正在加载模型: %s" % self.model_path)
            try:
                self.actor_critic = Policy(node_state_dim, num_node, num_type, base_kwargs={})
                model_file = os.path.join(self.model_path, "agent0.pth")
                if os.path.exists(model_file):
                    state_dict = torch.load(model_file, map_location="cpu")
                    self.actor_critic.load_state_dict(state_dict)
                    self.actor_critic.to(self.device)
                    self.actor_critic.eval()
                    print("[模型] DRL 模型已加载: %s" % model_file)
                else:
                    print("[模型] 模型文件不存在: %s" % model_file)
                    print("[模型] 将使用最短路径算法")
                    self.actor_critic = None
            except Exception as e:
                print("[模型] 加载失败: %s" % e)
                print("[模型] 将使用最短路径算法")
                self.actor_critic = None
        else:
            print("[模型] 未指定模型路径，将使用最短路径算法")

        print("[初始化] 完成！拓扑: %s, 节点数: %d, Agent数: %d"
              % (topo_name, num_node, num_agent))

    # ============================================================
    #  判断节点是否在 DRL 模型范围内
    # ============================================================
    def _in_drl_range(self, dpid):
        """
        判断 1-based DPID 是否在 DRL 模型的训练范围内。
        DRL 模型训练的节点为 0-based: 0 ~ num_node-1，
        对应 1-based DPID: 1 ~ num_node。
        """
        return 1 <= dpid <= self.num_node

    # ============================================================
    #  DRL 相关方法（保持原有逻辑不变）
    # ============================================================
    def _reset_env_with_request(self, src_node, dst_node, rtype=0, demand=100, duration=50):
        """按指定请求重建环境状态，避免 env.reset() 生成随机请求导致观测与请求不一致。
            参数：
            src_node: 源节点（0-based）
            dst_node: 目标节点（0-based）
            rtype: 请求类型（0-3）
            demand: 带宽需求（Kbps）
            duration: 请求持续时间（秒）
        """
        self.env._time_step = 0
        self.env._request_heapq = []
        self.env._link_usage = [([0.] * self.num_node) for _ in range(self.num_node)]
        self.env._delay_normal = [([1.] * self.num_node) for _ in range(self.num_node)]
        self.env._loss_normal = [([1.] * self.num_node) for _ in range(self.num_node)]

        import random as _random
        import numpy as _np

        orig_random_sample = _random.sample
        orig_random_choice = _random.choice
        orig_np_choice = _np.random.choice
        try:
            def _fixed_sample(population, k):
                return [src_node, dst_node]

            def _fixed_choice(seq):
                if isinstance(seq, (list, tuple)) and len(seq) > 0:
                    return seq[0]
                return orig_random_choice(seq)

            def _fixed_np_choice(a, p=None):
                if hasattr(a, '__iter__'):
                    return rtype
                return orig_np_choice(a, p=p)

            _random.sample = _fixed_sample
            _random.choice = _fixed_choice
            _np.random.choice = _fixed_np_choice

            self.env._update_state(pre_train=True)

            self.env._request.s = src_node
            self.env._request.t = dst_node
            self.env._request.rtype = rtype
            self.env._request.demand = demand
            self.env._request.start_time = 0
            self.env._request.end_time = duration

            return self.env._request, self.env._states
        finally:
            _random.sample = orig_random_sample
            _random.choice = orig_random_choice
            _np.random.choice = orig_np_choice

    def _sanitize_path(self, path, src_node, dst_node):
        """
        清洗 DRL 生成路径，避免环路/断链导致的重复包和高时延。
        """
        if not path:
            return self.env.calcSHR(src_node, dst_node)

        # 强制起点
        if path[0] != src_node:
            path = [src_node] + [n for n in path if n != src_node]

        # 消环
        simple_path = []
        pos = {}
        for node in path:
            if node in pos:
                keep = pos[node] + 1
                simple_path = simple_path[:keep]
                pos = {n: i for i, n in enumerate(simple_path)}
            else:
                pos[node] = len(simple_path)
                simple_path.append(node)

        path = simple_path

        # 强制终点
        if path[-1] != dst_node:
            tail = self.env.calcSHR(path[-1], dst_node)
            if tail and len(tail) > 1:
                path.extend(tail[1:])

        # 最终校验
        if len(path) != len(set(path)):
            return self.env.calcSHR(src_node, dst_node)

        for u, v in zip(path[:-1], path[1:]):
            if v not in self.env._link_lists[u]:
                return self.env.calcSHR(src_node, dst_node)

        if path[0] != src_node or path[-1] != dst_node:
            return self.env.calcSHR(src_node, dst_node)

        return path

    def compute_path_with_drl(self, src_node, dst_node):
        """使用 DRL 模型计算路径（0-based 索引）"""
        self._last_model_action_used = False
        if self.actor_critic is None:
            return self.env.calcSHR(src_node, dst_node)

        try:
            request, obses = self._reset_env_with_request(src_node, dst_node, rtype=0, demand=100, duration=50)

            path = [src_node]
            curr_path = [0] * self.num_node
            curr_path[src_node] = 1

            curr_agent, initial_path = self.env.first_agent()
            if initial_path:
                path = initial_path.copy()
                for k in initial_path:
                    curr_path[k] = 1

            if dst_node in path:
                return self._sanitize_path(path, src_node, dst_node)

            agents_flag = [0] * self.num_agent
            deterministic = True

            while curr_agent is not None and agents_flag[curr_agent] != 1:
                agents_flag[curr_agent] = 1

                condition_state = (
                    torch.tensor(curr_path, dtype=torch.float32)
                    .unsqueeze(-1)
                    .to(self.device)
                )

                edge_index = (
                    torch.tensor(
                        self.edge_indexs[self.agent_to_node[curr_agent]],
                        dtype=torch.long,
                    )
                    .t()
                    .contiguous()
                    .to(self.device)
                )

                obs = (
                    torch.tensor(obses[curr_agent], dtype=torch.float32)
                    .unsqueeze(0)
                    .to(self.device)
                )

                inputs = Data(x=obs, edge_index=edge_index)

                adj_mask = torch.tensor(
                    self.adj_masks[self.agent_to_node[curr_agent]],
                    dtype=torch.float32,
                ).to(self.device)
                rtype = torch.tensor([request.rtype], dtype=torch.long).to(self.device)

                with torch.no_grad():
                    value, action, action_log_prob = self.actor_critic.act(
                        inputs,
                        condition_state.unsqueeze(0),
                        self.agent_to_node[curr_agent],
                        rtype,
                        adj_mask,
                        deterministic=deterministic,
                    )
                self._last_model_action_used = True

                next_agent, path_segment = self.env.next_agent(curr_agent, action)

                if path_segment:
                    for node in path_segment:
                        if node not in path:
                            path.append(node)
                            curr_path[node] = 1

                        if dst_node in path:
                            break

                curr_agent = next_agent

            if not path:
                path = [src_node]
            if path[0] != src_node:
                path.insert(0, src_node)
            if path[-1] != dst_node:
                remaining = self.env.calcSHR(path[-1], dst_node)
                if remaining and len(remaining) > 1:
                    path.extend(remaining[1:])

            return self._sanitize_path(path, src_node, dst_node)

        except Exception as e:
            print("[DRL] 计算失败: %s" % e)
            import traceback
            traceback.print_exc()
            return self.env.calcSHR(src_node, dst_node)

    # ============================================================
    #  对外接口：统一路径计算入口
    # ============================================================
    def compute_path(self, src_node, dst_node, topo_edges=None):
        """
        对外接口：统一路径计算
        
        参数：
            src_node: 源交换机 DPID（1-based）
            dst_node: 目标交换机 DPID（1-based）
            topo_edges: 控制器传来的拓扑边列表 [[src, dst], ...]（1-based DPID）
                        当 DRL 无法处理时用于 Dijkstra 回退
        
        返回：
            决策字典，包含路径、真实决策来源、模型是否执行以及 fallback 原因。
        
        策略：
            1. src 和 dst 都在 DRL 范围内 → 走 DRL
            2. 任一节点超出 DRL 范围 → 走 Dijkstra（需要 topo_edges）
            3. DRL 失败 → 回退 Dijkstra
            4. Dijkstra 也失败 → 返回直连 [src, dst]
        """
        src_in_range = self._in_drl_range(src_node)
        dst_in_range = self._in_drl_range(dst_node)

        # ---- 情况1：两端都在 DRL 范围内，尝试 DRL ----
        if src_in_range and dst_in_range:
            try:
                src_0based = src_node - 1
                dst_0based = dst_node - 1

                if self.actor_critic is not None:
                    path_0based = self.compute_path_with_drl(src_0based, dst_0based)
                    decision_source = "drl_model" if self._last_model_action_used else "drl_shr"
                    fallback_reason = None if self._last_model_action_used else "model_action_not_used"
                    model_used = self._last_model_action_used
                else:
                    path_0based = self.env.calcSHR(src_0based, dst_0based)
                    decision_source = "drl_shr"
                    fallback_reason = "model_not_loaded"
                    model_used = False

                if path_0based:
                    path_1based = [node + 1 for node in path_0based]
                    print("[路径] %s 计算: %d -> %d = %s" % (decision_source, src_node, dst_node, path_1based))
                    return _decision(decision_source, path_1based, model_used=model_used,
                                     fallback_reason=fallback_reason)
            except Exception as e:
                print("[路径] DRL 异常，回退 Dijkstra: %s" % e)

        # ---- 情况2/3：DRL 范围外 或 DRL 失败，走 Dijkstra ----
        if topo_edges:
            path = _dijkstra_on_edges(topo_edges, src_node, dst_node)
            if path:
                print("[路径] Dijkstra 计算: %d -> %d = %s" % (src_node, dst_node, path))
                return _decision("dijkstra", path, model_used=False,
                                 fallback_reason="out_of_drl_range_or_drl_failed")
            else:
                print("[路径] Dijkstra 无路径: %d -> %d" % (src_node, dst_node))
        else:
            # 没有 topo_edges 且 DRL 范围外，尝试用 NetEnv 内部最短路径（仅限范围内）
            if src_in_range and dst_in_range:
                try:
                    path_0based = self.env.calcSHR(src_node - 1, dst_node - 1)
                    if path_0based:
                        path_1based = [node + 1 for node in path_0based]
                        print("[路径] NetEnv SHR 回退: %d -> %d = %s" % (src_node, dst_node, path_1based))
                        return _decision("drl_shr", path_1based, model_used=False,
                                         fallback_reason="no_topo_edges")
                except Exception:
                    pass
            print("[路径] 无 topo_edges 且超出 DRL 范围: %d -> %d" % (src_node, dst_node))

        print("[路径] 无可用路径: %d -> %d" % (src_node, dst_node))
        return _decision("none", None, model_used=False, fallback_reason="no_path")

    # ============================================================
    #  TCP 服务
    # ============================================================
    def run(self):
        """启动 TCP 服务（多线程版本）"""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(10)
        print("[服务] 已启动，监听端口 %d" % self.port)
        print("[服务] 等待控制器的路径计算请求...")
        print("[服务] DRL 节点范围: 1 ~ %d (0-based: 0 ~ %d)" % (self.num_node, self.num_node - 1))
        print("[服务] 超出范围的节点将使用 Dijkstra 回退（需控制器传入 topo_edges）")

        def handle_client(conn, addr):
            """处理单个客户端连接（长连接，支持多个请求）"""
            buffer = ""
            try:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    buffer += data.decode("utf-8")
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            request = json.loads(line)
                        except json.JSONDecodeError as e:
                            print("[错误] JSON 解析失败: %s" % e)
                            continue

                        if request.get("type") == "path_request":
                            src_node = int(request["src_node"])
                            dst_node = int(request["dst_node"])
                            request_id = request.get("request_id")
                            topo_edges = request.get("topo_edges", None)
                            candidates = request.get("candidates") or []
                            route_mode = request.get("route_mode", "unknown")

                            print("[请求] %d -> %d (ID: %s, topo_edges: %s)"
                                  % (src_node, dst_node, request_id,
                                     "%d条" % len(topo_edges) if topo_edges else "无"))
                            print("[请求] route_mode=%s candidates=%d task=%s policy=%s"
                                  % (route_mode, len(candidates),
                                     request.get("task_type", "default"),
                                     request.get("route_policy", "shortest_path")))

                            start_time = time.time()
                            decision = self.compute_path(src_node, dst_node, topo_edges)
                            elapsed = time.time() - start_time
                            path = decision.get("path") if isinstance(decision, dict) else decision

                            response = {
                                "type": "path_response",
                                "status": "ok" if path else "error",
                                "path": path,
                                "request_id": request_id,
                                "compute_time": elapsed,
                                "decision_source": decision.get("decision_source", "unknown"),
                                "model_used": decision.get("model_used", False),
                                "fallback_reason": decision.get("fallback_reason"),
                                "confidence": decision.get("confidence"),
                                "candidate_count": len(candidates),
                            }
                            if not path:
                                response["error"] = "no path"
                            print("[响应] 路径: %s (耗时: %.3fs)" % (path, elapsed))

                        elif request.get("type") == "batch_path_request":
                            requests = request.get("requests", [])
                            request_id = request.get("request_id")
                            topo_edges = request.get("topo_edges", None)

                            print("[批量请求] 共 %d 条路径 (ID: %s)" % (len(requests), request_id))

                            start_time = time.time()
                            paths = []
                            for req in requests:
                                src = int(req["src_node"])
                                dst = int(req["dst_node"])
                                decision = self.compute_path(src, dst, topo_edges)
                                path = decision.get("path") if isinstance(decision, dict) else decision
                                paths.append({
                                    "src": src,
                                    "dst": dst,
                                    "path": path,
                                    "decision_source": decision.get("decision_source", "unknown"),
                                    "model_used": decision.get("model_used", False),
                                    "fallback_reason": decision.get("fallback_reason"),
                                    "confidence": decision.get("confidence"),
                                })
                            elapsed = time.time() - start_time

                            response = {
                                "type": "batch_path_response",
                                "status": "ok",
                                "paths": paths,
                                "request_id": request_id,
                                "compute_time": elapsed
                            }
                            print("[批量响应] 完成 %d 条路径 (耗时: %.3fs)" % (len(paths), elapsed))

                        else:
                            response = {
                                "type": "path_response",
                                "status": "error",
                                "error": "未知的请求类型",
                                "request_id": request.get("request_id"),
                            }

                        conn.send((json.dumps(response) + '\n').encode("utf-8"))
            except Exception as e:
                print("[错误] 处理连接失败: %s" % e)
                import traceback
                traceback.print_exc()
            finally:
                try:
                    conn.close()
                except:
                    pass
                print("[连接] 客户端断开: %s" % str(addr))

        # 主循环
        while True:
            try:
                conn, addr = s.accept()
                client_thread = threading.Thread(target=handle_client, args=(conn, addr))
                client_thread.daemon = True
                client_thread.start()
            except Exception as e:
                print("[错误] 接受连接失败: %s" % e)
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DRL 路径计算服务 v2")
    parser.add_argument("--topo", default="Military", help="拓扑名称")
    parser.add_argument("--port", type=int, default=8889, help="监听端口")
    parser.add_argument(
        "--model",
        default=None,
        help="DRL 模型路径。相对路径按 drl-or-s 目录解析，默认使用 ./model/Military_mininet",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("DRL 路径计算服务 v2")
    print("=" * 60)

    service = DRLPathService(args.topo, args.port, args.model)
    try:
        service.run()
    except KeyboardInterrupt:
        print("\n[服务] 已停止")
