"""纯标准库最小 WebSocket 客户端（RFC 6455 客户端角色）。

用途：连接 ZCode 的 Chrome DevTools WebSocket 端点。只实现够用的子集：
- HTTP/1.1 Upgrade 握手 + Sec-WebSocket-Accept 校验
- 客户端帧带掩码发送文本
- 接收文本/二进制，自动应答 ping，处理 close
- 处理分片（continuation）消息
- 不发送 Origin 头（新版 Chromium 对未授权 origin 的调试 WS 连接会拒绝）

零第三方依赖：socket / hashlib / base64 / os / struct
"""

import base64
import hashlib
import os
import socket
import struct
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 帧 opcode
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(Exception):
    pass


class WebSocket:
    """最小 WebSocket 客户端。仅支持发送文本、接收文本/二进制。"""

    def __init__(self, host, port, path="/", timeout=10.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._closed = False
        self._do_handshake(host, port, path)

    # ---------- 握手 ----------

    def _do_handshake(self, host, port, path):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("utf-8"))

        # 读取响应头（最多 64KB）
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("握手时连接被关闭")
            data += chunk

        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0]
        if " 101 " not in status:
            raise WebSocketError(f"握手失败: {status}")

        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()

        expect = base64.b64encode(
            hashlib.sha1((key + GUID).encode("utf-8")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expect:
            raise WebSocketError("Sec-WebSocket-Accept 校验失败")

        # 可能有多余的已读字节（紧跟响应头的服务端帧）
        self._buffer = rest

    # ---------- 发送 ----------

    def send_text(self, text: str):
        payload = text.encode("utf-8")
        self._send_frame(OP_TEXT, payload)

    def send_binary(self, data: bytes):
        self._send_frame(OP_BIN, data)

    def _send_frame(self, opcode, payload: bytes):
        if self._closed:
            raise WebSocketError("连接已关闭")

        mask_key = os.urandom(4)
        header = bytearray([0x80 | opcode])  # FIN=1

        length = len(payload)
        if length < 126:
            header.append(0x80 | length)  # MASK=1
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)

        header += mask_key
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    # ---------- 接收 ----------

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            if self._buffer:
                take = min(n - len(buf), len(self._buffer))
                buf += self._buffer[:take]
                self._buffer = self._buffer[take:]
                continue
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketError("连接已关闭")
            buf += chunk
        # 一次读多了，多余字节放回缓冲，供后续帧使用
        if len(buf) > n:
            self._buffer = buf[n:] + self._buffer
            buf = buf[:n]
        return buf

    def recv_message(self):
        """接收一条完整消息。返回 (opcode, payload)。自动应答 ping。"""
        if self._closed:
            raise WebSocketError("连接已关闭")

        first = self._read_exact(2)
        fin = bool(first[0] & 0x80)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]

        mask_key = self._read_exact(4) if masked else None
        payload = self._read_exact(length)
        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == OP_PING:
            self._send_frame(OP_PONG, payload)  # 自动应答
            return self.recv_message()
        if opcode == OP_PONG:
            return self.recv_message()
        if opcode == OP_CLOSE:
            self._closed = True
            raise WebSocketError("对端发送了 Close 帧")

        # 分片消息：组装 continuation
        data = payload
        if not fin:
            while True:
                cont = self._read_exact(2)
                cfin = bool(cont[0] & 0x80)
                copcode = cont[0] & 0x0F
                if copcode != OP_CONT:
                    raise WebSocketError("分片消息中出现非 continuation 帧")
                cmasked = bool(cont[1] & 0x80)
                clen = cont[1] & 0x7F
                if clen == 126:
                    clen = struct.unpack(">H", self._read_exact(2))[0]
                elif clen == 127:
                    clen = struct.unpack(">Q", self._read_exact(8))[0]
                cmask = self._read_exact(4) if cmasked else None
                cdata = self._read_exact(clen)
                if cmask:
                    cdata = bytes(b ^ cmask[i % 4] for i, b in enumerate(cdata))
                data += cdata
                if cfin:
                    break

        return opcode, data

    def recv_text(self):
        opcode, payload = self.recv_message()
        if opcode == OP_BIN:
            return payload
        return payload.decode("utf-8")

    def close(self):
        try:
            if not self._closed:
                self._send_frame(OP_CLOSE, b"")
        except Exception:
            pass
        self._closed = True
        try:
            self._sock.close()
        except Exception:
            pass


def _self_test():
    """对 wsclient 的握手/掩码/收发做回显自测。"""
    import threading

    # 一个最小 WebSocket 服务端（仅用于自测）
    def echo_server():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        events.append(("port", port))

        conn, _ = srv.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        key = [l.split(": ", 1)[1] for l in head.split("\r\n")[1:] if l.lower().startswith("sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(
            f"HTTP/1.1 101 Switching Protocols\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode("latin-1")
        )

        def read_frame():
            h = b""
            while len(h) < 2:
                h += conn.recv(2 - len(h))
            ln = h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", conn.recv(2))[0]
            mask = conn.recv(4)
            payload = b""
            while len(payload) < ln:
                payload += conn.recv(ln - len(payload))
            return bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        msg = read_frame()
        # 回显
        masked = bytearray([0x82, len(msg)])
        conn.sendall(bytes(masked) + msg)
        conn.close()
        srv.close()

    events = []
    t = threading.Thread(target=echo_server, daemon=True)
    t.start()
    while not events:
        time.sleep(0.02)
    port = events[0][1]

    ws = WebSocket("127.0.0.1", port, "/")
    ws.send_text("hello ws")
    op, payload = ws.recv_message()
    assert op == OP_BIN or op == OP_TEXT, op
    assert payload == b"hello ws", payload
    ws.close()
    print("wsclient 自测通过")


if __name__ == "__main__":
    _self_test()
