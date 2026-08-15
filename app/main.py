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


# ---------- GUI ----------

class WallpaperGUI:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        root.title("ZCode 壁纸启动器")
        root.geometry("560x560")
        root.minsize(520, 520)

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        # --- 图片路径 ---
        row = ttk.Frame(frm); row.pack(fill="x", **pad)
        ttk.Label(row, text="壁纸图片：", width=10).pack(side="left")
        self.img_var = tk.StringVar(value=self.cfg.get("wallpaper", ""))
        ttk.Entry(row, textvariable=self.img_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self.browse_image).pack(side="left", padx=4)

        # --- 预览 ---
        self.preview = tk.Label(frm, text="（选择图片后此处显示预览；仅 PNG/GIF 可内置预览）",
                                bg="#202020", fg="#999", height=6)
        self.preview.pack(fill="x", **pad)
        self._photo = None

        # --- ZCode 路径 ---
        row = ttk.Frame(frm); row.pack(fill="x", **pad)
        ttk.Label(row, text="ZCode 程序：", width=10).pack(side="left")
        self.zc_var = tk.StringVar(value=self.cfg.get("zcode_path", ""))
        ttk.Entry(row, textvariable=self.zc_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self.browse_zcode).pack(side="left", padx=4)

        # --- 端口 ---
        row = ttk.Frame(frm); row.pack(fill="x", **pad)
        ttk.Label(row, text="调试端口：", width=10).pack(side="left")
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", 9333)))
        ttk.Entry(row, textvariable=self.port_var, width=10).pack(side="left")
        ttk.Label(row, text="   样式模式：").pack(side="left", padx=(12, 0))
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "cover"))
        ttk.Combobox(row, textvariable=self.mode_var, values=MODES, state="readonly", width=8).pack(side="left")

        # --- 位置 / 暗化 ---
        row = ttk.Frame(frm); row.pack(fill="x", **pad)
        ttk.Label(row, text="图片位置：", width=10).pack(side="left")
        self.pos_var = tk.StringVar(value=self.cfg.get("position", "center"))
        ttk.Combobox(row, textvariable=self.pos_var, values=POSITIONS, state="readonly", width=10).pack(side="left")
        ttk.Label(row, text="   暗化：").pack(side="left", padx=(12, 0))
        self.darken_var = tk.DoubleVar(value=float(self.cfg.get("darken", 0.0)))
        ttk.Scale(row, from_=0.0, to=0.9, variable=self.darken_var, length=140).pack(side="left")
        self.darken_label = ttk.Label(row, text=f"{self.darken_var.get():.0%}")
        self.darken_label.pack(side="left", padx=4)
        self.darken_var.trace_add("write", lambda *a: self.darken_label.config(text=f"{self.darken_var.get():.0%}"))

        # --- 高级：透明化选择器 ---
        ttk.Label(frm, text="高级：透明化容器选择器（逗号分隔的 CSS 选择器，让壁纸在这些区域透出）").pack(anchor="w", **pad)
        self.sel_var = tk.StringVar(value=", ".join(self.cfg.get("transparent_selectors", [])))
        ttk.Entry(frm, textvariable=self.sel_var).pack(fill="x", **pad)

        # --- 按钮 ---
        row = ttk.Frame(frm); row.pack(fill="x", **pad)
        ttk.Button(row, text="保存配置", command=self.on_save).pack(side="left", padx=4)
        ttk.Button(row, text="启动/重启 ZCode（带壁纸）", command=self.on_launch).pack(side="left", padx=4)

        # --- 日志 ---
        self.log = tk.Text(frm, height=8, state="disabled", bg="#1b1b1b", fg="#d4d4d4")
        self.log.pack(fill="both", expand=True, **pad)
        self.log_scroll = ttk.Scrollbar(frm, command=self.log.yview)
        self.log.config(yscrollcommand=self.log_scroll.set)

        self.append_log("就绪。选择图片后点「启动/重启 ZCode（带壁纸）」。")
        self.refresh_preview()

    # ---------- 工具 ----------

    def append_log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def refresh_preview(self):
        path = self.img_var.get()
        self._photo = None
        if path and os.path.exists(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in (".png", ".gif"):
                try:
                    self._photo = tk.PhotoImage(file=path)
                    # 缩放过大时等比缩小预览
                    w, h = self._photo.width(), self._photo.height()
                    max_w, max_h = 540, 120
                    if w > max_w or h > max_h:
                        ratio = min(max_w / w, max_h / h)
                        nw, nh = max(1, int(w * ratio)), max(1, int(h * ratio))
                        self._photo = self._photo.subsample(int(w / nw) or 1, int(h / nh) or 1)
                    self.preview.config(image=self._photo, text="", bg="#202020")
                except Exception:
                    self.preview.config(image="", text="（该图片无法用 tkinter 预览，可换 PNG/GIF）")
            else:
                self.preview.config(image="", text=f"（{ext} 格式无法内置预览，仅 PNG/GIF 支持；已选择该文件）")
        else:
            self.preview.config(image="", text="（选择图片后此处显示预览；仅 PNG/GIF 可内置预览）")

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
