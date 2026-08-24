"""网页终端：WebSocket ↔ 设备（telnet 原始套接字 / SSH shell）的字节桥。

拓扑图上右键「登录设备」直接开一个交互式会话 —— 走的是设备表里的
接入参数，与诊断采集共用同一套凭据，不另外配。

telnet 侧要做最小的选项协商：把对端发来的 IAC DO/WILL 一律回 WONT/DONT，
把 IAC 序列从数据流里剥掉，避免终端里出现乱码；SSH 侧用 paramiko 的
invoke_shell，并按前端上报的尺寸调 PTY。
"""
from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import service

router = APIRouter()

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


class _TelnetBridge:
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=8)
        self.sock.settimeout(None)
        self._pending = b""

    def _negotiate(self, data: bytes) -> bytes:
        """剥 IAC；对 DO/WILL 回 WONT/DONT（我们什么选项都不支持，纯字节流）。"""
        buf = self._pending + data
        self._pending = b""
        out = bytearray()
        i = 0
        while i < len(buf):
            b = buf[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(buf):
                self._pending = buf[i:]
                break
            cmd = buf[i + 1]
            if cmd == IAC:
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    self._pending = buf[i:]
                    break
                opt = buf[i + 2]
                if cmd == DO:
                    self.sock.sendall(bytes([IAC, WONT, opt]))
                elif cmd == WILL:
                    self.sock.sendall(bytes([IAC, DONT, opt]))
                i += 3
            elif cmd == SB:
                end = buf.find(bytes([IAC, SE]), i)
                if end < 0:
                    self._pending = buf[i:]
                    break
                i = end + 2
            else:
                i += 2
        return bytes(out)

    def read(self) -> Optional[bytes]:
        """None = 对端关闭；b"" = 这一包只有协商没有正文（不是关闭）。"""
        data = self.sock.recv(4096)
        if not data:
            return None
        return self._negotiate(data)

    def write(self, data: bytes) -> None:
        self.sock.sendall(data.replace(b"\xff", b"\xff\xff"))

    def resize(self, cols: int, rows: int) -> None:
        pass

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class _SshBridge:
    def __init__(self, host: str, port: int, username: str, password: str):
        import paramiko
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(host, port=port, username=username, password=password,
                            timeout=8, look_for_keys=False, allow_agent=False)
        self.chan = self.client.invoke_shell(term="xterm", width=120, height=32)
        self.chan.settimeout(None)

    def read(self) -> Optional[bytes]:
        data = self.chan.recv(4096)
        return data if data else None

    def write(self, data: bytes) -> None:
        self.chan.send(data)

    def resize(self, cols: int, rows: int) -> None:
        try:
            self.chan.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.chan.close()
            self.client.close()
        except Exception:
            pass


def _open(device: Dict[str, Any]):
    proto = (device.get("protocol") or "ssh").lower()
    if proto == "telnet":
        return _TelnetBridge(device["host"], int(device.get("port") or 23))
    return _SshBridge(device["host"], int(device.get("port") or 22),
                      device.get("username") or "", device.get("password") or "")


@router.websocket("/api/devices/{device_id}/terminal")
async def terminal(ws: WebSocket, device_id: int) -> None:
    await ws.accept()
    device = service.get_device(device_id, reveal=True)
    if not device:
        await ws.send_text("\r\n设备不存在\r\n")
        await ws.close()
        return
    loop = asyncio.get_event_loop()
    try:
        bridge = await loop.run_in_executor(None, _open, device)
    except Exception as exc:
        await ws.send_text("\r\n连接失败: {0}\r\n".format(exc))
        await ws.close()
        return

    async def pump_device() -> None:
        try:
            while True:
                data = await loop.run_in_executor(None, bridge.read)
                if data is None:
                    await ws.send_text("\r\n[连接已由设备关闭]\r\n")
                    break
                if data:
                    await ws.send_bytes(data)
        except Exception:
            pass

    task = asyncio.ensure_future(pump_device())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await loop.run_in_executor(None, bridge.write, msg["bytes"])
            elif msg.get("text"):
                text = msg["text"]
                # 控制帧：{"resize": [cols, rows]}；其余当作输入
                if text.startswith("{"):
                    try:
                        ctl = json.loads(text)
                        if "resize" in ctl:
                            cols, rows = ctl["resize"]
                            bridge.resize(int(cols), int(rows))
                            continue
                    except (ValueError, TypeError):
                        pass
                await loop.run_in_executor(None, bridge.write, text.encode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        bridge.close()
