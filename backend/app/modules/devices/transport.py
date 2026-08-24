"""设备接入 —— SSH / Telnet / 模拟器，同一接口。

确定性要点：
  · 登录后统一关分屏，否则回显会被 ---- More ---- 打断
  · 命令回显要剥掉命令本身与提示符，残留字符会直接改变快照哈希
  · 固定退避重试，**不加随机 jitter** —— jitter 会把「采到 / 没采到」变成概率事件
"""
from __future__ import annotations

import re
import socket
import time
from typing import Any, Dict, List, Optional

# 常见的分页关闭命令，按厂商猜；设备记录里可以显式覆盖
PAGER_CMDS = [
    "screen-length disable",          # H3C Comware
    "terminal length 0",              # Cisco / FRR 风格
    "screen-length 0 temporary",      # 华为
]
# 提示符：行尾是 > # $，或 <name> / <name(view)> 形式
# **必须读到提示符才算这条命令结束** —— 只等"安静一会儿"是不够的：
# 按需启动的模块响应慢时会读到空回显，同一条命令时快时慢就成了不确定性来源。
PROMPT_RE = re.compile(r"(?:^|\n)[^\n]{0,120}?[>#\$]\s*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MORE_RE = re.compile(r"-+\s*more\s*-+|<--- More --->", re.I)

# 设备明确拒绝这条命令 —— 这不是数据，是「这台设备没有这条命令」。
# 必须判为采集失败，否则错误文本会当成证据喂给模型。
# 注意区分：「target module is not running」是**有效信息**（模块没起来），要保留。
REJECT_RE = re.compile(
    r"(?im)^\s*(?:%\s*)?(?:Error:\s*)?("
    r"Invalid command|Unrecognized command|Incomplete command|Ambiguous command|"
    r"Unknown command|Invalid input|Syntax error|command not found"
    r")\b.*$")


def clean(text: str, command: str) -> str:
    """剥掉 ANSI、控制字符、分页提示、命令回显与末尾提示符。"""
    out = ANSI_RE.sub("", text)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = MORE_RE.sub("", out)
    out = CTRL_RE.sub("", out)
    lines = out.split("\n")
    # 第一行通常是命令回显
    if lines and command.strip() and command.strip() in lines[0]:
        lines = lines[1:]
    # 最后一行通常是提示符
    while lines and re.match(r"^[^\s]{0,80}[>#\$]\s*$", lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip("\n")


class BaseTransport:
    name = "base"

    def connect(self) -> None:
        raise NotImplementedError

    def run(self, command: str) -> Dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SshTransport(BaseTransport):
    name = "ssh"

    def __init__(self, host: str, port: int, username: str, password: str,
                 pager_cmd: str = "", timeout: float = 20.0) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.pager_cmd, self.timeout = pager_cmd, timeout
        self.client = None
        self.chan = None

    def connect(self) -> None:
        import paramiko
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(self.host, port=self.port, username=self.username,
                            password=self.password, timeout=self.timeout,
                            look_for_keys=False, allow_agent=False)
        self.chan = self.client.invoke_shell(width=512, height=1000)
        self.chan.settimeout(self.timeout)
        time.sleep(0.8)
        self._drain()
        for cmd in ([self.pager_cmd] if self.pager_cmd else PAGER_CMDS):
            self._send(cmd)

    def _read_until_prompt(self, hard: float = 20.0, quiet: float = 0.35) -> str:
        """读到提示符为止；始终读不到就等安静期兜底，但会带回 saw_prompt 标记。"""
        out, start, last = "", time.time(), time.time()
        while time.time() - start < hard:
            if self.chan.recv_ready():
                out += self.chan.recv(65535).decode("utf-8", errors="replace")
                last = time.time()
                if PROMPT_RE.search(ANSI_RE.sub("", out).replace("\r", "")):
                    return out
            else:
                if out and time.time() - last > quiet * 4:
                    break
                time.sleep(0.05)
        return out

    def _flush(self) -> None:
        """丢弃缓冲区里的残留字节。

        不做这一步，上一条命令的尾巴会被算进下一条的回显 —— 命令与回显错位，
        同一条命令时而干净时而带着别人的内容，这是最难查的一类不一致。
        """
        deadline = time.time() + 0.4
        while time.time() < deadline:
            if self.chan.recv_ready():
                self.chan.recv(65535)
                deadline = time.time() + 0.2
            else:
                time.sleep(0.03)

    def _send(self, command: str) -> str:
        self._flush()
        self.chan.send(command + "\n")
        return self._read_until_prompt()

    def run(self, command: str) -> Dict[str, Any]:
        try:
            raw = self._send(command)
        except Exception as exc:
            return {"ok": False, "text": "", "error": str(exc)}
        return finish(raw, command)

    def close(self) -> None:
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass


class TelnetTransport(BaseTransport):
    """Telnet 接入。直接用 socket 实现，不依赖 telnetlib（3.13 已移除）。"""

    name = "telnet"

    def __init__(self, host: str, port: int, username: str, password: str,
                 pager_cmd: str = "", timeout: float = 20.0) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.pager_cmd, self.timeout = pager_cmd, timeout
        self.sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)
        banner = self._drain(quiet=1.2)
        low = banner.lower()
        if self.username and ("username" in low or "login" in low):
            self._send_raw(self.username)
            low = self._drain(quiet=1.0).lower()
        if self.password and "password" in low:
            self._send_raw(self.password)
            self._drain(quiet=1.0)
        for cmd in ([self.pager_cmd] if self.pager_cmd else PAGER_CMDS):
            self._send_raw(cmd)
            self._drain(quiet=0.5)

    def _drain(self, quiet: float = 0.6, hard: float = 15.0,
               until_prompt: bool = False) -> str:
        chunks: List[bytes] = []
        deadline, start = time.time() + quiet, time.time()
        while time.time() < deadline and time.time() - start < hard:
            try:
                self.sock.settimeout(0.2)
                data = self.sock.recv(65535)
                if not data:
                    break
                chunks.append(self._strip_iac(data))
                deadline = time.time() + quiet
                if until_prompt:
                    text = b"".join(chunks).decode("utf-8", errors="replace")
                    if PROMPT_RE.search(ANSI_RE.sub("", text).replace("\r", "")):
                        break
            except socket.timeout:
                continue
            except OSError:
                break
        return b"".join(chunks).decode("utf-8", errors="replace")

    @staticmethod
    def _strip_iac(data: bytes) -> bytes:
        """剔除 Telnet 协商字节（IAC 0xFF 序列），只留纯文本。"""
        out, i = bytearray(), 0
        while i < len(data):
            if data[i] == 0xFF:
                if i + 1 < len(data) and data[i + 1] in (0xFB, 0xFC, 0xFD, 0xFE):
                    i += 3
                    continue
                if i + 1 < len(data) and data[i + 1] == 0xFA:
                    j = data.find(b"\xff\xf0", i)
                    i = (j + 2) if j >= 0 else len(data)
                    continue
                i += 2
                continue
            out.append(data[i])
            i += 1
        return bytes(out)

    def _flush(self) -> None:
        """丢弃缓冲区里的残留字节，避免命令与回显错位。"""
        deadline = time.time() + 0.4
        while time.time() < deadline:
            try:
                self.sock.settimeout(0.15)
                if not self.sock.recv(65535):
                    return
                deadline = time.time() + 0.2
            except socket.timeout:
                return
            except OSError:
                return

    def _send_raw(self, text: str) -> None:
        self.sock.sendall((text + "\r\n").encode("utf-8"))

    def run(self, command: str) -> Dict[str, Any]:
        try:
            self._flush()
            self._send_raw(command)
            # 读到提示符为止 —— 按需模块响应慢时，只等安静期会读到空回显
            raw = self._drain(quiet=0.4, hard=20.0, until_prompt=True)
        except Exception as exc:
            return {"ok": False, "text": "", "error": str(exc)}
        return finish(raw, command)

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


def finish(raw: str, command: str) -> Dict[str, Any]:
    """统一收尾：没读到提示符 或 清洗后为空，都判为「未采到」。

    空回显绝不能当成有效数据 —— 同一条命令时而有内容时而为空，
    正是最难查的一类不一致来源。宁可标记缺失，交给 F5 兜底。
    """
    saw_prompt = bool(PROMPT_RE.search(ANSI_RE.sub("", raw).replace("\r", "")))
    text = clean(raw, command)
    reject = REJECT_RE.search(text)
    if reject:
        return {"ok": False, "text": "", "unsupported": True,
                "error": "设备不支持该命令：{0}".format(reject.group(0).strip()[:60])}
    if not text.strip():
        return {"ok": False, "text": "",
                "error": "无回显（{0}）".format(
                    "已见提示符，命令可能无输出" if saw_prompt else "未读到提示符，可能超时")}
    if not saw_prompt:
        return {"ok": False, "text": text, "error": "未读到提示符，回显可能被截断"}
    return {"ok": True, "text": text, "error": ""}


def open_for(device: Dict[str, Any]) -> BaseTransport:
    proto = (device.get("protocol") or "ssh").lower()
    if proto == "telnet":
        return TelnetTransport(device["host"], device["port"] or 23,
                               device["username"], device["password"],
                               device.get("pager_cmd", ""))
    return SshTransport(device["host"], device["port"] or 22,
                        device["username"], device["password"],
                        device.get("pager_cmd", ""))
