"""网页终端的 telnet 桥：IAC 协商剥离与 EOF 判定。"""
from app.modules.devices import terminal as T


class _FakeSock:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""

    def recv(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data):
        self.sent += data

    def settimeout(self, t):
        pass

    def close(self):
        pass


def _bridge(chunks):
    b = T._TelnetBridge.__new__(T._TelnetBridge)
    b.sock = _FakeSock(chunks)
    b._pending = b""
    return b


def test_negotiation_stripped_and_refused():
    # IAC DO ECHO(1) + IAC WILL SGA(3) + 正文
    b = _bridge([bytes([255, 253, 1, 255, 251, 3]) + b"Welcome\r\n"])
    out = b.read()
    assert out == b"Welcome\r\n"
    # 回 WONT ECHO / DONT SGA —— 我们什么选项都不支持
    assert b.sock.sent == bytes([255, 252, 1, 255, 254, 3])


def test_negotiation_only_packet_is_not_eof():
    b = _bridge([bytes([255, 253, 1]), b"prompt> "])
    assert b.read() == b""          # 只有协商 → 空正文，但不是关闭
    assert b.read() == b"prompt> "
    assert b.read() is None         # 对端真正关闭


def test_split_iac_across_packets():
    b = _bridge([b"ab" + bytes([255]), bytes([253, 1]) + b"cd"])
    assert b.read() == b"ab"
    assert b.read() == b"cd"


def test_escaped_iac_byte_kept():
    b = _bridge([bytes([255, 255]) + b"x"])
    assert b.read() == bytes([255]) + b"x"


def test_write_escapes_iac():
    b = _bridge([])
    b.write(b"a" + bytes([255]) + b"b")
    assert b.sock.sent == b"a" + bytes([255, 255]) + b"b"
