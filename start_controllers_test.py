#!/usr/bin/env python
"""
启动测试拓扑（creat_test_topo.py）对应的七个从控制器
- OpenFlow 监听端口: 6654, 6655, 6656, 6657, 6658, 6659, 6670
- 复用 start_controllers.py 中的 ControllerManager
- 根控由 server_agent.py 手动启动（默认监听 6001）

使用前请先启动: python3 server_agent.py

PID 文件: /tmp/ryu_controllers_test.pid
"""

import sys
import time
from pathlib import Path

from start_controllers import ControllerManager


TEST_CONTROLLER_PORTS = [6654, 6655, 6656, 6657, 6658, 6659, 6670]
DEFAULT_USE_TERMINAL = False


def build_manager(use_terminal=DEFAULT_USE_TERMINAL):
    manager = ControllerManager(
        base_port=6654,
        num_controllers=len(TEST_CONTROLLER_PORTS),
        controller_app='controller.py',
        use_terminal=use_terminal,
    )
    manager.pid_file = Path('/tmp/ryu_controllers_test.pid')
    return manager


def parse_args(argv):
    args = list(argv[1:])
    command = 'start'
    if args and not args[0].startswith('-'):
        command = args.pop(0).lower()

    use_terminal = DEFAULT_USE_TERMINAL
    if '--terminal' in args or '-t' in args:
        use_terminal = True
    if '--no-terminal' in args or '-n' in args:
        use_terminal = False

    return command, use_terminal


def start_selected(manager, ports):
    print('=' * 60)
    print('启动测试拓扑控制器')
    print('=' * 60)
    print('目标端口: %s' % ', '.join(str(p) for p in ports))
    print('应用: %s' % manager.controller_app)
    if manager.use_terminal and manager.terminal_cmd:
        print('终端: %s (每个控制器在独立终端中运行)' % manager.terminal_cmd[0])
    elif manager.use_terminal:
        print('终端: 未找到终端模拟器，将在后台运行')
    else:
        print('模式: 后台运行（日志输出到文件）')
    print('=' * 60)
    print()

    failed_ports = []
    for port in ports:
        process = manager.start_controller(port)
        if process:
            manager.processes[port] = process
        else:
            failed_ports.append(port)
        time.sleep(0.8)

    manager.save_pids()

    print()
    print('=' * 60)
    if failed_ports:
        print('警告: %s 个控制器启动失败: %s' % (len(failed_ports), failed_ports))
    else:
        print('✓ 所有 %s 个控制器已成功启动' % len(ports))
    print('=' * 60)
    print()
    manager.show_status()


def main():
    command, use_terminal = parse_args(sys.argv)
    manager = build_manager(use_terminal=use_terminal)

    if command == 'start':
        print('测试拓扑: 启动 7 个从控制器（6654,6655,6656,6657,6658,6659,6670）')
        print('请确保已先启动根控 server_agent.py\n')
        start_selected(manager, TEST_CONTROLLER_PORTS)
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
        print('测试拓扑 Ryu 从控状态')
        print('=' * 60)
        manager.show_status()
        print()

    elif command == 'restart':
        print('重启测试拓扑控制器...')
        manager.stop_all()
        time.sleep(2)
        start_selected(manager, TEST_CONTROLLER_PORTS)

    else:
        print('未知命令: %s' % command)
        print('用法: python3 start_controllers_test.py [start|stop|status|restart] [--no-terminal|-n] [--terminal|-t]')
        sys.exit(1)


if __name__ == '__main__':
    main()
