"""
该文件从原始 controller 大文件中拆分了主机数据模型（Host）功能，
用于统一表示“主机的 MAC/IP/接入端口”并提供序列化与比较能力。

函数/方法作用：
- Host.__init__：初始化主机对象。
- Host.to_dict：将主机对象转换为可传输字典。
- Host.__eq__：定义主机对象相等性（基于 MAC 和端口）。
- Host.__str__：定义主机对象字符串表示。
"""

class Host(object):
    # This is data class passed by EventHostXXX,EventHostXXX 类在特定事件发生时被触发，例如交换机连接、流表更新等。
    def __init__(self, mac, port, ipv4):
        super(Host, self).__init__()
        self.port = port
        self.mac = mac
        self.ipv4 = ipv4

    def to_dict(self):
        d = {
            'mac': self.mac,
            'ipv4': self.ipv4,
            'port': self.port.to_dict()
        }
        return d

    def __eq__(self, host):
        return self.mac == host.mac and self.port == host.port

    def __str__(self):
        msg = 'Host<mac=%s, port=%s,' % (self.mac, str(self.port))
        msg += ','.join(self.ipv4)
        msg += '>'
        return msg
