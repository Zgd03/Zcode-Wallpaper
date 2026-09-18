"""ZCode 壁纸 CDP 控制器。

职责：
1. 读取 config.json（图片路径、ZCode 路径、端口、样式参数）——见 config.py。
2. 起一个本地 HTTP 服务提供壁纸图片（避免 file:// 子资源被 Chromium 拦截）。
3. 以 --remote-debugging-port 启动 ZCode（或附加到已开调试端口的实例）。
4. 用 DevTools WebSocket 注入 inject/wallpaper.js：新文档注入 + 立即应用 + 周期心跳。
5. 单实例运行（controller.lock + 固定图片端口绑定）；监控 ZCode 退出后自行退出。

命令行（在项目根目录执行）：
  python app/controller.py                      # 常规：启动并注入
  python app/controller.py --attach             # 只附加已开的调试实例，不启动 ZCode
  python app/controller.py --probe              # 附加后 dump DOM（选透明化选择器用）
  python app/controller.py --shot out.png       # 注入后截图到 out.png

零第三方依赖。
"""

import base64
import http.server
import json
import mimetypes
import os
import socketserver
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from config import CONFIG_PATH, INJECT_PATH, LOCK_PATH, is_video, load_config
from wsclient import WebSocket, WebSocketError


# ---------- 单实例锁 ----------


def pid_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return "No tasks" not in out and str(pid) in out
    except Exception:
        return False


def acquire_lock():
    """单实例：若已有活着的控制器则退出；否则写 lock。"""
    if os.path.exists(LOCK_PATH):
        try:
            old = int(open(LOCK_PATH).read().strip())
            if pid_alive(old):
                print(f"[controller] 已有控制器(PID {old})在运行，本实例退出。", flush=True)
                return False
        except Exception:
            pass
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        if os.path.exists(LOCK_PATH):
            if int(open(LOCK_PATH).read().strip()) == os.getpid():
                os.remove(LOCK_PATH)
    except Exception:
        pass


# ---------- 本地图片服务 ----------


class MediaHandler(http.server.BaseHTTPRequestHandler):
    """提供壁纸的 HTTP 服务：支持图片与视频（含 Range 流式请求，供 <video> 播放）。"""

    wallpaper_path = None
    mime_type = "image/png"

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/wallpaper":
            self.send_response(404)
            self.end_headers()
            return
        fp = self.wallpaper_path
        if not fp or not os.path.exists(fp):
            self.send_response(404)
            self.end_headers()
            return

        size = os.path.getsize(fp)
        rng = self.headers.get("Range", "")

        if rng.startswith("bytes="):
            start_s, _, end_s = rng[6:].partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            start, end, length = 0, size - 1, size
            self.send_response(200)

        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", self.mime_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(length))
        self.end_headers()

        # 按需读取区间并流式写出（不整读大文件）
        with open(fp, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    # 客户端中途断开（切换/关闭视频、seek 都会发生，Windows 上是
                    # ConnectionAbortedError WinError 10053）：正常现象，直接停发，
                    # 否则 socketserver 会把整段 traceback 打进 controller.log。
                    break
                remaining -= len(chunk)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ImageServer:
    def __init__(self, port):
        self.port = port
        # 自定义 handler 类，类属性保存壁纸路径/类型，实例可读取
        self.Handler = type("WallpaperHandler", (MediaHandler,), {})
        self.Handler.wallpaper_path = None
        self.Handler.mime_type = "image/png"
        # 固定端口绑定 = 单实例硬保证（另一控制器会绑定失败退出）。
        # 端口未及时释放时（旧控制器刚被杀）短暂重试，避免竞态误判。
        self.httpd = None
        last_err = None
        for _ in range(10):
            try:
                self.httpd = ThreadingServer(("127.0.0.1", self.port), self.Handler)
                break
            except OSError as e:
                last_err = e
                time.sleep(0.5)
        if self.httpd is None:
            raise RuntimeError(
                f"图片服务端口 {self.port} 被占用——可能已有另一个控制器在运行。"
                f"（{last_err}）")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def set_wallpaper(self, path):
        mime, _ = mimetypes.guess_type(path)
        if not mime:
            mime = "video/mp4" if is_video(path) else "image/png"
        self.Handler.wallpaper_path = path
        self.Handler.mime_type = mime or "image/png"

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------- 注入脚本烘焙 ----------


def read_inject_source():
    with open(INJECT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def build_inject_source(cfg, image_url):
    """把配置烘焙进注入脚本源码。"""
    inject_cfg = {
        "url": image_url,
        "kind": "video" if is_video(cfg.get("wallpaper", "")) else "image",
        "mode": cfg["mode"],
        "position": cfg["position"],
        "repeat": cfg["repeat"],
        "darken": cfg["darken"],
        "transparentSelectors": cfg["transparent_selectors"],
        "backgroundOverrides": cfg.get("background_overrides", {}),
    }
    source = read_inject_source()
    # 末尾取一次诊断值：Runtime.evaluate 的返回值即最后一条语句的值，
    # 控制器据此判断 background_overrides 的选择器是否还命中（见 _report_diag）。
    return (
        "window.__zcodeWallpaperConfig = "
        + json.dumps(inject_cfg, ensure_ascii=False)
        + ";\n"
        + source
        + "\nwindow.__zcodeWallpaperDiag;"
    )


# ---------- CDP 客户端 ----------


class CDPClient:
    """对一个 page target 的 CDP 会话。"""

    def __init__(self, target_id, ws_url, title, url):
        self.target_id = target_id
        self.title = title
        self.url = url
        self.ws_url = ws_url
        self.ws = None
        self.next_id = 0
        self.pending = {}  # id -> {"event": threading.Event, "result": dict}
        self.reader = None

    def connect(self, timeout=10.0):
        from urllib.parse import urlparse

        p = urlparse(self.ws_url)
        self.ws = WebSocket(p.hostname, p.port, p.path or "/", timeout=timeout)
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        while True:
            try:
                op, payload = self.ws.recv_message()
            except socket.timeout:
                continue  # 超时不是断连，继续等待
            except (WebSocketError, OSError):
                # 连接断开：唤醒所有等待者
                for ev in list(self.pending.values()):
                    ev["event"].set()
                break
            if op == 0x1:
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if "id" in msg and msg["id"] in self.pending:
                    entry = self.pending.pop(msg["id"])
                    entry["result"] = msg
                    entry["event"].set()

    def send_and_get(self, method, params=None, timeout=15):
        """发送 CDP 命令并等待响应，返回响应 dict。"""
        self.next_id += 1
        mid = self.next_id
        entry = {"event": threading.Event(), "result": None}
        self.pending[mid] = entry
        self.ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not entry["event"].wait(timeout=timeout):
            self.pending.pop(mid, None)
            return {"error": {"message": "CDP 响应超时"}}
        return entry["result"]

    def evaluate(self, expression, timeout=15):
        """执行 JS 表达式，返回 result。"""
        return self.send_and_get(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
            timeout=timeout,
        )

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws = None


# ---------- 目标发现 ----------


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def list_targets(port):
    return fetch_json(f"http://127.0.0.1:{port}/json/list")


def is_page_target(t):
    if t.get("type") not in ("page", "webview"):
        return False
    url = t.get("url") or ""
    # 主渲染页 / 任意含 html 的页面都注入（多窗口）
    return ".html" in url or "file://" in url


# ---------- 注入逻辑 ----------


class WallpaperInjector:
    def __init__(self, cfg, image_server):
        self.cfg = cfg
        self.image_server = image_server
        self.clients = {}
        self.last_source = None
        self.last_diag = {}  # target_id -> 上次打印过的诊断，避免每 3 秒刷屏

    def make_source(self):
        # 带缓存破坏参数：换壁纸文件后 URL 变化，浏览器才会重新拉取
        wp = self.cfg.get("wallpaper", "")
        version = ""
        if wp and os.path.exists(wp):
            version = "?v=" + str(int(os.path.getmtime(wp)))
        url = f"http://127.0.0.1:{self.image_server.port}/wallpaper{version}"
        return build_inject_source(self.cfg, url)

    def attach(self, target):
        cid = target["id"]
        if cid in self.clients:
            return self.clients[cid]
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return None
        try:
            client = CDPClient(cid, ws_url, target.get("title", ""), target.get("url", ""))
            client.connect(timeout=5)
            client.send_and_get("Page.enable")
            self.clients[cid] = client
            self.inject(client)
            return client
        except Exception as e:
            print(f"[injector] 附加失败 {target.get('url','')[:60]}: {e}")
            return None

    def inject(self, client):
        """注册到新文档（配置变化时重新注册）+ 立即应用。"""
        source = self.make_source()
        # 注册到每个新文档；只有当 source 变化时才重新注册
        if source != self.last_source:
            try:
                client.send_and_get("Page.addScriptToEvaluateOnNewDocument", {"source": source})
                self.last_source = source
            except Exception:
                pass
        # 立即应用（幂等）
        try:
            res = client.evaluate(source)
            if res.get("exceptionDetails"):
                print(f"[injector] 应用异常: {res['exceptionDetails'].get('text','')}")
            else:
                self._report_diag(client, res)
        except Exception as e:
            print(f"[injector] evaluate 失败: {e}")

    def _report_diag(self, client, res):
        """把注入脚本的诊断写进日志：background_overrides 失效不再无声无息。

        诊断值形如 {"matched":1,"auto":0}——matched=0 表示配置里的选择器在当前
        ZCode 版本一个都没命中（类名变了），此时脚本会兜底自愈（auto>0）。
        """
        try:
            raw = res["result"]["result"].get("value")
        except Exception:
            return
        if not raw or self.last_diag.get(client.target_id) == raw:
            return
        self.last_diag[client.target_id] = raw
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            return
        if d.get("matched"):
            return  # 配置正常命中，不刷日志
        if d.get("auto"):
            print(f"[injector] 注意：background_overrides 的选择器在 ZCode 当前版本未命中，"
                  f"已自动把 {d['auto']} 个内容表面改为半透明兜底。"
                  f"建议按新版 DOM 更新 config.json 的选择器（python app/controller.py --probe）。",
                  flush=True)
        else:
            print("[injector] 警告：background_overrides 的选择器未命中，且没找到可兜底的内容表面——"
                  "壁纸可能被内容区盖住，请用 --probe 检查新版 DOM。", flush=True)

    def heartbeat(self, client):
        # 周期性全量重新注入：每次都用控制器当前的 source 覆盖页面里的
        # __zcodeWallpaperConfig，防止被旧配置/旧控制器污染导致样式回退。
        try:
            res = client.evaluate(self.make_source())
            if not res.get("exceptionDetails"):
                self._report_diag(client, res)
        except Exception:
            pass

    def refresh(self):
        """刷新目标列表：附加新 target，剔除已关闭的。"""
        try:
            targets = list_targets(self.cfg["port"])
        except Exception:
            return
        alive = set()
        for t in targets:
            if is_page_target(t):
                c = self.attach(t)
                if c:
                    alive.add(c.target_id)
        for cid in list(self.clients):
            if cid not in alive:
                try:
                    self.clients[cid].close()
                except Exception:
                    pass
                self.clients.pop(cid, None)

    def run(self, stop_event):
        self.last_source = None
        cfg_mtime = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
        while not stop_event.is_set():
            # 热重载 config.json（换图/改样式无需重启控制器）
            mt = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
            if mt != cfg_mtime:
                cfg_mtime = mt
                old_wp = self.cfg.get("wallpaper")
                self.cfg = load_config()
                if self.cfg.get("wallpaper") and self.cfg.get("wallpaper") != old_wp:
                    self.image_server.set_wallpaper(self.cfg["wallpaper"])
                print(f"[controller] 配置已重载: wallpaper={self.cfg.get('wallpaper')}", flush=True)

            self.refresh()
            # 心跳：抵抗 React 重渲染/断连
            for c in list(self.clients.values()):
                self.heartbeat(c)
            # 检查配置变化（换图/改样式）→ 重新注入并注册新文档脚本
            src = self.make_source()
            if src != self.last_source:
                for c in list(self.clients.values()):
                    self.inject(c)
            stop_event.wait(3)


# ---------- 探测 / 截图 ----------


PROBE_SCRIPT = r"""
(function () {
  function cls(el) {
    var c = el.className;
    if (c && c.baseVal !== undefined) c = c.baseVal;
    return String(c || '').split(/\s+/).filter(Boolean).join('.');
  }
  function info(el, depth) {
    if (depth > 6) return;
    var cs = getComputedStyle(el);
    var line = '  '.repeat(depth) + el.tagName.toLowerCase() + '#' + (el.id || '') + '.' + cls(el)
      + ' bg=' + cs.backgroundColor
      + (cs.backgroundImage !== 'none' ? ' bgimg=' + cs.backgroundImage.slice(0, 30) : '')
      + (depth === 0 ? '' : '');
    lines.push(line);
    for (var i = 0; i < Math.min(el.children.length, 20); i++) info(el.children[i], depth + 1);
  }
  var lines = [];
  var r = document.getElementById('root');
  if (!r) return 'NO #root';
  info(r, 0);
  lines.push('---- injected? ----');
  lines.push('layer=' + (!!document.getElementById('zcode-wallpaper-layer')));
  lines.push('style-el=' + (!!document.getElementById('zcode-wallpaper-style')));
  lines.push('apply-fn=' + (typeof window.__zcodeWallpaperApply));
  lines.push('diag=' + window.__zcodeWallpaperDiag);
  return lines.join('\n');
})()
"""


def probe(client, wait=20):
    """等待 #root 渲染完成再 dump DOM（附带注入状态）。"""
    t0 = time.time()
    while time.time() - t0 < wait:
        res = client.evaluate("(document.getElementById('root') && document.getElementById('root').children.length) ? 1 : 0")
        try:
            if res["result"]["result"]["value"]:
                break
        except Exception:
            pass
        time.sleep(1)
    res = client.evaluate(PROBE_SCRIPT)
    try:
        print(res["result"]["result"]["value"])
    except Exception:
        print(json.dumps(res, ensure_ascii=False)[:2000])


def screenshot(client, out_path):
    res = client.send_and_get("Page.captureScreenshot", {"format": "png"})
    try:
        b64 = res["result"]["data"]
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64))
        print(f"[shot] 已保存 {out_path}")
    except Exception:
        print("截图失败:", json.dumps(res, ensure_ascii=False)[:500])


# ---------- 主流程 ----------


def wait_for_cdp(port, timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            targets = list_targets(port)
            if targets:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def is_zcode_running(cfg):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {os.path.basename(cfg['zcode_path'])}"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        return os.path.basename(cfg["zcode_path"]).lower() in out.lower()
    except Exception:
        return False


def _start_image_server(cfg):
    """启动本地图片服务；失败（端口被占）返回 None。"""
    try:
        server = ImageServer(cfg["image_port"])
    except RuntimeError as e:
        print(f"[controller] {e}")
        return None
    server.set_wallpaper(cfg["wallpaper"])
    server.start()
    print(f"[controller] 壁纸图片服务 http://127.0.0.1:{server.port}/wallpaper  <- {cfg['wallpaper']}")
    return server


def _ensure_zcode(cfg, port, attach_only):
    """确保 CDP 调试端口就绪。返回 (proc, ok)。

    ok=False 表示应退出；此时若 proc 非空说明是我们启动的 ZCode（已 terminate）。
    """
    if wait_for_cdp(port, timeout=3):
        return None, True
    if attach_only:
        print("[controller] --attach 模式：ZCode 未开调试端口，退出。")
        return None, False
    if is_zcode_running(cfg):
        print("[controller] ZCode 已在运行但未开调试端口。请先退出，再用本启动器启动。")
        return None, False
    args = [cfg["zcode_path"], f"--remote-debugging-port={port}", "--remote-allow-origins=*"]
    print("[controller] 启动 ZCode:", " ".join(args))
    proc = subprocess.Popen(args, cwd=os.path.dirname(cfg["zcode_path"]))
    if not wait_for_cdp(port, timeout=60):
        print("[controller] 等待 ZCode 调试端口超时。")
        proc.terminate()
        return proc, False
    return proc, True


def _run_session(server, injector, proc, port, do_probe, do_shot):
    """注入 + （probe/shot/心跳）。返回退出码。"""
    injector.refresh()
    if not injector.clients:
        print("[controller] 未找到可注入的页面 target。")
        try:
            targets = list_targets(port)
            print("当前 targets:", json.dumps(
                [{k: t.get(k) for k in ('id', 'type', 'title', 'url')} for t in targets],
                ensure_ascii=False))
        except Exception:
            pass
        return 5

    if do_probe:
        for c in injector.clients.values():
            print("=== probe:", c.title, "===")
            probe(c)
        return 0
    if do_shot:
        for c in injector.clients.values():
            screenshot(c, do_shot)
            break
        return 0

    print(f"[controller] 已注入 {len(injector.clients)} 个页面，心跳保持中 (Ctrl+C 退出)")
    stop = threading.Event()
    down = 0
    try:
        threading.Thread(target=injector.run, args=(stop,), daemon=True).start()
        while True:
            if proc is not None:
                if proc.poll() is not None:
                    print("[controller] ZCode 已退出。")
                    break
            else:
                # 附加模式：监听调试端口失联，判断 ZCode 退出（防僵尸进程）。
                # 端口是浏览器级调试端口，只在 ZCode 进程死亡时才断开，2 次即可判定。
                if wait_for_cdp(port, timeout=1):
                    down = 0
                else:
                    down += 1
                    if down >= 2:
                        print("[controller] ZCode 已退出（调试端口失联）。")
                        break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for c in injector.clients.values():
            c.close()
    return 0


def main(argv):
    cfg = load_config()
    attach_only = "--attach" in argv
    do_probe = "--probe" in argv
    do_shot = None
    if "--shot" in argv:
        do_shot = argv[argv.index("--shot") + 1] if len(argv) > argv.index("--shot") + 1 else "wallpaper.png"

    if not cfg.get("wallpaper") or not os.path.exists(cfg["wallpaper"]):
        print("[controller] 配置里没有有效壁纸图片（config.json 的 wallpaper 字段）。")
        return 2

    if not acquire_lock():
        return 0

    server = _start_image_server(cfg)
    if server is None:
        release_lock()
        return 0

    port = cfg["port"]
    proc, ok = _ensure_zcode(cfg, port, attach_only)
    if not ok:
        server.stop()
        release_lock()
        return 4 if proc is not None else 3

    print(f"[controller] DevTools 已就绪 127.0.0.1:{port}")
    injector = WallpaperInjector(cfg, server)
    code = _run_session(server, injector, proc, port, do_probe, do_shot)

    server.stop()
    release_lock()
    print("[controller] 退出。")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
