#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode 壁纸启动器（tkinter GUI + CLI）。

功能：
  1. 选择一张图片作为 ZCode 背景壁纸。
  2. 配置持久化到项目根 config.json。
  3. 「启动/重启 ZCode」：结束已运行的 ZCode，以 CDP 调试端口方式启动，
     并挂载 controller.py 持续注入壁纸（升级 ZCode 不影响本定制）。

用法（在项目根目录执行）：
  python app/main.py                 # GUI
  python app/main.py --cli launch    # 无界面：按配置结束并重启 ZCode + 注入壁纸

零第三方依赖（tkinter / subprocess / os / sys）。
"""

import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import CONTROLLER_PATH, LOCK_PATH, LOG_PATH, load_config, save_config

MODES = ["cover", "contain", "fill", "tile"]
POSITIONS = ["center", "top", "bottom", "left", "right", "top left", "top right", "bottom left", "bottom right"]


def zcode_running(exe_path):
    name = os.path.basename(exe_path).lower()
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return name in out.lower()
    except Exception:
        return False


def kill_zcode(exe_path, log=None):
    """结束所有同名 ZCode 进程树，等待退出。"""
    name = os.path.basename(exe_path)
    if not zcode_running(exe_path):
        return True
    if log:
        log("ZCode 正在运行，正在结束进程…")
    subprocess.run(["taskkill", "/IM", name, "/T", "/F"],
                   capture_output=True, text=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        if not zcode_running(exe_path):
            return True
        time.sleep(0.5)
    return not zcode_running(exe_path)


def kill_controllers():
    """结束所有已运行的壁纸控制器（lock 记录 + 扫描兜底），并等端口释放。

    避免多控制器同时注入互相覆盖（壁纸 URL 在两个端口间跳变会导致闪屏）。
    """
    if os.path.exists(LOCK_PATH):
        try:
            pid = int(open(LOCK_PATH, encoding="utf-8").read().strip())
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        except Exception:
            pass
    try:
        # 兜底扫描：按控制器脚本的完整路径匹配（避免误杀其它 controller.py）
        pattern = f"*{CONTROLLER_PATH}*"
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '{pattern}' }} | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True,
        )
    except Exception:
        pass
    # 等旧控制器的图片端口释放，避免新控制器绑定失败（单实例）
    img_port = int(load_config().get("image_port", 18765))
    deadline = time.time() + 10
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", img_port))
            s.close()
            time.sleep(0.3)
        except OSError:
            break


def spawn_controller(log=None):
    """以独立进程启动控制器（GUI 关闭后壁纸保持注入）。"""
    logf = open(LOG_PATH, "a", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, "-u", CONTROLLER_PATH],
        cwd=os.path.dirname(CONTROLLER_PATH), stdout=logf, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
    )
    if log:
        log("控制器已启动（日志：controller.log）。ZCode 即将带壁纸打开。")


# ---------- CLI ----------

def cli_launch(cfg):
    if not cfg.get("wallpaper") or not os.path.exists(cfg["wallpaper"]):
        print("错误：config.json 中没有有效壁纸图片路径。请先运行 GUI 选择图片。")
        return 2
    if not os.path.exists(cfg["zcode_path"]):
        print(f"错误：找不到 ZCode：{cfg['zcode_path']}")
        return 2
    if zcode_running(cfg["zcode_path"]):
        print("正在结束已运行的 ZCode …")
        if not kill_zcode(cfg["zcode_path"]):
            print("未能结束 ZCode。")
            return 3
    kill_controllers()
    print("启动控制器（启动 ZCode + 注入壁纸）…")
    from controller import main as controller_main
    return controller_main([])


# ---------- GUI（现代暗色主题） ----------

# 主题配色
BG       = "#1a1b1e"   # 窗口背景
SURFACE  = "#222327"   # 卡片/面板
SURFACE2 = "#2a2b30"   # 输入框 / 次级按钮
SURFACE3 = "#323339"   # 悬停
BORDER   = "#3a3b42"   # 边框
TEXT     = "#e9eaee"   # 主文字
MUTED    = "#9aa0a8"   # 次要文字
ACCENT   = "#6c8cff"   # 强调色
ACCENT_H = "#7d9aff"   # 强调色悬停
ACCENT_D = "#5876e8"   # 强调色按下
OK       = "#3fb950"
WARN     = "#d29922"
ERR      = "#f85149"

FONT       = "Segoe UI"
FONT_SEMI  = "Segoe UI Semibold"
FONT_MONO  = "Consolas"

# 应用图标（内嵌 base64 PNG，64x64 圆角 "Z"）
ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAADKUlEQVR4nO2ba0sUURjHf/PsbkpRYmJJRWL1Iix6URmZFVpkGkVU7yTpAxRBIH2HwBe98AsEXSi6v8rK0iSi7GIXL12liF5EYV7yXsYwLjOs47Zuzs6ZPf1gl3N2mfOc/3+euezseQyS4Ejt+DgKUldjGNPdxgi66H81w0hGeOFyTqEg7e85NF0jjETFqyo6ETPimWAkstdjxbs5rQJ/m6ebEUaie11V0VMx1dxjTZDpDhAUnHOOd/iK294Puvh4JsQe3hLvbB9k8fE0OLVK7JdRp9JBfJSoFrdDQdxSP53ET2VCVLOgOYLmGDqkv5NYnYLmCJojaI6gOWGvBs6IQO3RmRtvcBiO16FvBtx6FLAMGAfGfiW/fThkt7u+QEPLjExrchw8YmQUjp1MbtuydbC/1GqPjsHpG/DboyeSgmIsyIY9m+3+9Wb42u1dPEEhxICDFRCZyMt3n6HpqccxUYjtRVCwyGoPj1qp7/WzeEER8nJg1ya7f6URvvd4H1dQABGorrTP/J0f4f6LFMVGAco3wNKF9g3P2frUxRZ8ZnEuVBTb/Ut3obtPEwNCISv1QxOzePUBHraldg6Cj1QWWxlgMjAE526mfg6CT+TnwY4iu3+hAXp/amJAOGTd8Jhnf5PWN/Ck04+Z4I8Bu0us675J3wCcv41vSKoDmnd629bbfVN8/yB6GBAJQ3UFRP+fbemA52/xFUllsL1bIDfbavf0w8U7+I6kKtCKJbB1rd03L3nmpU8LAzIi1lk/ujLhwUto60IJJBVB9pVCTpbV7u6Fy40og3gdYGU+lKyx2uZv+zP1MDSCHgZkzoKqnXa/uRVef0IpxMvBD5RB9lyr/e0HXLuHcoS9GnjVMti42u7Pz4ITh5Mfr+kZXG0iGAbMzoSq8skPPMXxrH+6RH8yzzTixaCFBTBvDoEg7MWgjzusVxAQNEfQHEFzBM2R6OrpeKsp03WFWF2NYfzPADRHzDcdDgO39MctA9LRhHirYCVePU06mOCmwalV3L5ItNxEddwqXxKuGWoPuAmJlv0Ybh9qXTbnRNvCSSdal8460bZ4GhdUNSOZ8vk/PyUd3kOqPqgAAAAASUVORK5CYII="


def _font(size, weight="normal"):
    return (FONT_SEMI if weight == "bold" else FONT, size)


def _set_dark_titlebar(root):
    """Windows 10/11 下把原生标题栏设为深色。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def _mk_label(parent, text="", color=TEXT, size=10, weight="normal"):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color,
                    font=_font(size, weight), anchor="w")


def _mk_entry(parent, var, width=30):
    return tk.Entry(parent, textvariable=var, width=width, relief="flat", bd=0,
                    bg=SURFACE2, fg=TEXT, insertbackground=TEXT, font=_font(10),
                    highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)


def _mk_button(parent, text, command, kind="secondary", padx=14, pady=7):
    """扁平按钮，带悬停/按下反馈。"""
    if kind == "primary":
        normal, hover, press, fg = ACCENT, ACCENT_H, ACCENT_D, "#ffffff"
    elif kind == "danger":
        normal, hover, press, fg = "#c93a3a", "#d94a4a", "#b02f2f", "#ffffff"
    else:
        normal, hover, press, fg = SURFACE2, SURFACE3, "#24252a", TEXT
    btn = tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                    bg=normal, fg=fg, activebackground=press, activeforeground=fg,
                    font=_font(10, "bold" if kind == "primary" else "normal"),
                    padx=padx, pady=pady, cursor="hand2", highlightthickness=0)
    btn.bind("<Enter>", lambda e, b=btn, h=hover: b.configure(bg=h))
    btn.bind("<Leave>", lambda e, b=btn, n=normal: b.configure(bg=n))
    return btn


def _mk_card(parent):
    return tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)


def _mk_section(parent, title):
    """小节标题：竖条强调色 + 文字。"""
    row = tk.Frame(parent, bg=BG)
    tk.Frame(row, bg=ACCENT, width=3, height=14).pack(side="left", padx=(0, 7))
    tk.Label(row, text=title, bg=BG, fg=TEXT, font=_font(11, "bold")).pack(side="left")
    return row


def _round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class WallpaperGUI:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        root.title("ZCode 壁纸启动器")
        root.geometry("620x700")
        root.minsize(580, 640)
        root.configure(bg=BG)
        try:
            root.iconphoto(True, tk.PhotoImage(data=ICON_B64))
        except Exception:
            pass

        self._style_ttk(root)

        # 字段变量
        self.img_var = tk.StringVar(value=self.cfg.get("wallpaper", ""))
        self.zc_var = tk.StringVar(value=self.cfg.get("zcode_path", ""))
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", 9333)))
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "cover"))
        self.pos_var = tk.StringVar(value=self.cfg.get("position", "center"))
        self.darken_var = tk.DoubleVar(value=float(self.cfg.get("darken", 0.0)))
        self.sel_var = tk.StringVar(value=", ".join(self.cfg.get("transparent_selectors", [])))
        self._photo = None

        self._build(root)
        _set_dark_titlebar(root)

        self.darken_var.trace_add("write", lambda *a: self._update_darken_label())
        self.img_var.trace_add("write", lambda *a: self.refresh_preview())

        self.append_log("就绪。选择图片后点「启动 / 重启 ZCode（带壁纸）」。")
        self.refresh_preview()

    # ---------- 主题 ----------

    def _style_ttk(self, root):
        st = ttk.Style(root)
        st.theme_use("clam")
        st.configure("Dark.TCombobox",
                     fieldbackground=SURFACE2, background=SURFACE2, foreground=TEXT,
                     arrowcolor=MUTED, bordercolor=BORDER, lightcolor=BORDER,
                     darkcolor=BORDER, padding=4, font=_font(10))
        st.configure("Dark.Horizontal.TScale",
                     troughcolor=SURFACE2, background=BG, bordercolor=BG,
                     lightcolor=ACCENT, darkcolor=ACCENT, sliderlength=14)
        root.option_add("*TCombobox*Listbox.background", SURFACE3)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    # ---------- 构建 ----------

    def _build(self, root):
        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=16)

        # 头部
        tk.Label(outer, text="ZCode 壁纸启动器", bg=BG, fg=TEXT,
                 font=(FONT_SEMI, 17)).pack(anchor="w")
        tk.Label(outer, text="给 ZCode 桌面客户端换一张好看的背景", bg=BG, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w", pady=(2, 0))

        # 预览卡片
        prev = _mk_card(outer)
        prev.pack(fill="x", pady=(14, 2))
        self.preview_canvas = tk.Canvas(prev, bg=SURFACE, height=170, highlightthickness=0)
        self.preview_canvas.pack(fill="x", padx=1, pady=1)

        # 壁纸图片 / ZCode 程序
        self._field_row(outer, "壁纸图片", self.img_var, self.browse_image)
        self._field_row(outer, "ZCode 程序", self.zc_var, self.browse_zcode)

        # 样式
        _mk_section(outer, "样式").pack(fill="x", pady=(16, 0))
        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text="模式", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        ttk.Combobox(row, textvariable=self.mode_var, values=MODES, state="readonly",
                     style="Dark.TCombobox", width=9).pack(side="left", padx=(8, 24))
        tk.Label(row, text="位置", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        ttk.Combobox(row, textvariable=self.pos_var, values=POSITIONS, state="readonly",
                     style="Dark.TCombobox", width=11).pack(side="left", padx=(8, 0))

        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="暗化", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        ttk.Scale(row, from_=0.0, to=0.9, variable=self.darken_var,
                  style="Dark.Horizontal.TScale", length=180).pack(side="left", padx=(8, 10))
        self.darken_label = tk.Label(row, text="", bg=BG, fg=ACCENT,
                                     font=_font(10, "bold"))
        self.darken_label.pack(side="left")

        # 高级
        _mk_section(outer, "高级").pack(fill="x", pady=(16, 0))
        self._field_row(outer, "调试端口", self.port_var, None)
        self._field_row(outer, "透明化容器", self.sel_var, None)

        # 操作
        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(16, 0))
        _mk_button(row, "保存配置", self.on_save).pack(side="left")
        _mk_button(row, "启动 / 重启 ZCode（带壁纸）", self.on_launch,
                   kind="primary").pack(side="right")

        # 日志
        log_card = _mk_card(outer)
        log_card.pack(fill="x", pady=(14, 0))
        self.log = tk.Text(log_card, height=6, state="disabled", bg="#141519",
                           fg="#b6bac2", font=(FONT_MONO, 9), relief="flat", bd=0,
                           padx=10, pady=8, highlightthickness=0)
        self.log.pack(fill="x")

    def _field_row(self, parent, label, var, browse_cmd=None):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text=label, bg=BG, fg=MUTED, font=_font(10),
                 width=8, anchor="w").pack(side="left")
        e = _mk_entry(row, var)
        e.pack(side="left", fill="x", expand=True, ipady=5)
        if browse_cmd:
            _mk_button(row, "浏览…", browse_cmd, padx=12, pady=5).pack(side="left", padx=(8, 0))
        return row

    # ---------- 工具 ----------

    def _update_darken_label(self):
        self.darken_label.config(text=f"{int(round(self.darken_var.get() * 100))}%")

    def append_log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------- 预览 ----------

    def refresh_preview(self):
        c = self.preview_canvas
        c.delete("all")
        self._photo = None
        cw = c.winfo_width() or 580
        ch = c.winfo_height() or 170
        path = self.img_var.get().strip()

        if path and os.path.exists(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in (".png", ".gif"):
                try:
                    ph = tk.PhotoImage(file=path)
                    w, h = ph.width(), ph.height()
                    ratio = min((cw - 36) / max(w, 1), (ch - 36) / max(h, 1))
                    if ratio < 1:
                        sx = max(1, int(round(w * ratio)) or 1)
                        sy = max(1, int(round(h * ratio)) or 1)
                        ph = ph.subsample(int(w / sx) or 1, int(h / sy) or 1)
                    self._photo = ph
                    c.create_image((cw or 580) // 2, ch // 2, image=ph)
                    c.create_text(12, ch - 14, anchor="w", text="当前壁纸预览",
                                  fill=MUTED, font=(FONT, 9))
                    return
                except Exception:
                    pass
            self._draw_placeholder(f"已选择：{os.path.basename(path)}",
                                   "该格式无法内置预览，仅 PNG / GIF 支持")
        else:
            self._draw_placeholder("选择一张图片作为 ZCode 背景",
                                   "支持 PNG / JPG / BMP / GIF / WebP")

    def _draw_placeholder(self, line1, line2):
        c = self.preview_canvas
        cw = c.winfo_width() or 580
        ch = c.winfo_height() or 170
        c.delete("all")
        cx, cy = cw // 2, (ch - 30) // 2
        # 图片图标：圆角相框 + 太阳 + 山
        _round_rect(c, cx - 30, cy - 26, cx + 30, cy + 26, 8,
                    outline="#46484f", width=2, fill="")
        c.create_oval(cx + 10, cy - 18, cx + 20, cy - 8, outline="#5a5d63", width=2)
        c.create_polygon(cx - 20, cy + 6, cx - 4, cy - 14, cx + 12, cy + 6,
                         fill="", outline="#5a5d63", width=2)
        c.create_polygon(cx - 2, cy + 6, cx + 8, cy - 8, cx + 20, cy + 6,
                         fill="", outline="#5a5d63", width=2)
        c.create_text(cx, cy + 44, text=line1, fill=MUTED, font=(FONT, 10))
        c.create_text(cx, cy + 62, text=line2, fill="#5a5d63", font=(FONT, 9))

    # ---------- 事件 ----------

    def browse_image(self):
        path = filedialog.askopenfilename(
            title="选择壁纸图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("所有文件", "*.*")],
        )
        if path:
            self.img_var.set(path)
            self.refresh_preview()

    def browse_zcode(self):
        path = filedialog.askopenfilename(title="选择 ZCode.exe",
                                          filetypes=[("程序", "*.exe"), ("所有文件", "*.*")])
        if path:
            self.zc_var.set(path)

    def collect_config(self):
        """以磁盘上最新的 config.json 为基底，只覆盖 GUI 管理的字段。

        绝不丢弃 GUI 未管理的字段（如 background_overrides），否则用户在 GUI
        里保存一次配置就会把后来手工加的高级规则冲掉。
        """
        selectors = [s.strip() for s in self.sel_var.get().split(",") if s.strip()]
        latest = load_config()  # 重新读盘，避免使用过期的 self.cfg
        cfg = dict(latest)
        cfg.update({
            "zcode_path": self.zc_var.get().strip(),
            "wallpaper": self.img_var.get().strip(),
            "port": int(self.port_var.get() or 9333),
            "image_port": int(latest.get("image_port", 18765)),
            "mode": self.mode_var.get(),
            "position": self.pos_var.get(),
            "repeat": latest.get("repeat", "no-repeat"),
            "darken": round(float(self.darken_var.get()), 3),
            "transparent_selectors": selectors,
        })
        return cfg

    def on_save(self):
        try:
            cfg = self.collect_config()
        except ValueError:
            messagebox.showerror("配置错误", "端口必须是数字。")
            return
        save_config(cfg)
        self.cfg = cfg
        self.append_log(f"配置已保存：{cfg['wallpaper'] or '（未设置壁纸）'}")

    def on_launch(self):
        try:
            cfg = self.collect_config()
        except ValueError:
            messagebox.showerror("配置错误", "端口必须是数字。")
            return
        save_config(cfg)
        self.cfg = cfg

        if not cfg["wallpaper"] or not os.path.exists(cfg["wallpaper"]):
            messagebox.showwarning("缺少壁纸", "请先选择一张壁纸图片。")
            return
        if not os.path.exists(cfg["zcode_path"]):
            messagebox.showerror("找不到 ZCode", f"找不到：{cfg['zcode_path']}")
            return

        self.append_log(f"壁纸：{cfg['wallpaper']}")
        self.append_log(f"ZCode：{cfg['zcode_path']}")
        threading.Thread(target=self._launch_worker, args=(cfg,), daemon=True).start()

    def _launch_worker(self, cfg):
        if zcode_running(cfg["zcode_path"]):
            self.append_log("ZCode 正在运行，正在结束进程…")
            if not kill_zcode(cfg["zcode_path"]):
                self.append_log("结束 ZCode 失败，请手动关闭后重试。")
                return
            self.append_log("已结束。")
        kill_controllers()
        self.append_log("已清理旧控制器。")
        spawn_controller(self.append_log)
        self.append_log("完成。ZCode 将带壁纸打开（日志见 controller.log）。")


def main():
    cfg = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        if len(sys.argv) > 2 and sys.argv[2] == "launch":
            sys.exit(cli_launch(cfg))
        print("用法：python main.py --cli launch")
        sys.exit(0)
    root = tk.Tk()
    WallpaperGUI(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
