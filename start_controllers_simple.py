#!/usr/bin/env python
"""
启动简单拓扑的三个从控制器（与 start_controllers.py 共用 ControllerManager）
- 仅启动 OpenFlow 端口 6654、6655、6656（对应 create_simple_topo 三域）
- 根控由 server_agent.py 手动启动（默认监听 5001）；从控通过 controller.py 内 TCP 连接根控

使用前请先启动: python3 server_agent.py（或你的根控入口）
从控连接地址由环境变量控制（与 controller.py 中 SERVER_CONFIG 一致）:
  SERVER_AGENT_IP   默认 127.0.0.1
  SERVER_AGENT_PORT 默认 5001

PID 文件: /tmp/ryu_controllers_simple.pid
"""

import sys
import time
from pathlib import Path

from start_controllers import ControllerManager


def main():
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
    else:
        command = 'start'

    use_terminal = True
    if '--no-terminal' in sys.argv or '-n' in sys.argv:
        use_terminal = False
        if '--no-terminal' in sys.argv:
            sys.argv.remove('--no-terminal')
        if '-n' in sys.argv:
            sys.argv.remove('-n')

    manager = ControllerManager(
        base_port=6654,
        num_controllers=3,
        controller_app='controller.py',
        use_terminal=use_terminal,
    )
    manager.pid_file = Path('/tmp/ryu_controllers_simple.pid')

    if command == 'start':
        print('简单拓扑: 仅启动 3 个从控制器 OpenFlow 6654～6656')
        print('请确保已先启动根控 server_agent.py（从控将连接 SERVER_AGENT_IP:SERVER_AGENT_PORT）\n')
        manager.start_all()
        try:
            print('运行中... Ctrl+C 停止全部')
            while True:
                time.sleep(1)
                for port in list(manager.processes.keys()):
                    process = manager.processes[port]
                    if process.poll() is not None:
                        print('警告: 控制器 (端口 %s) 意外退出' % port)
                        del manager.processes[port]
        except KeyboardInterrupt:
            print('\n\n收到中断信号，正在停止所有控制器...')
            manager.stop_all()

    elif command == 'stop':
        manager.stop_all()

    elif command == 'status':
        print('=' * 60)
        print('简单拓扑 Ryu 从控状态（6654-6656）')
        print('=' * 60)
        manager.show_status()
        print()

    elif command == 'restart':
        print('重启简单拓扑控制器...')
        manager.stop_all()
        time.sleep(2)
        manager.start_all()

    else:
        print('未知命令: %s' % command)
        print('用法: python3 start_controllers_simple.py [start|stop|status|restart] [--no-terminal|-n]')
        sys.exit(1)


if __name__ == '__main__':
    main()
