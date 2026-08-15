"""共享配置：路径常量 + 默认配置 + 读写。

main.py 与 controller.py 都从这里导入，避免配置在多个文件里重复定义而互相漂移。

路径说明：本文件位于 app/ 下，项目根是它的上一级；config.json / 运行时产物
（controller.lock / controller.log）放在项目根，inject 脚本在 app/inject/。

load_config 合并语义：config.json 中「非空值」覆盖默认值；缺省/空值保留默认。
高级字段（transparent_selectors / background_overrides）以 config.json 里存的为准，
因此 GUI 保存时必须把整个 config.json 当基底（见 main.py 的 collect_config）。

wallpaper / zcode_path 若填相对路径，按项目根解析（便于克隆后直接用 demo 壁纸）。
"""

import json
import os

# 视为「动态壁纸」的视频扩展名
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi"}


def is_video(path):
    """按扩展名判断是否为视频（动态壁纸）。"""
    return os.path.splitext(str(path))[1].lower() in VIDEO_EXTS


# 本文件位于 app/ 下：项目根是它的上一级
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(APP_DIR)
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
INJECT_PATH = os.path.join(APP_DIR, "inject", "wallpaper.js")
CONTROLLER_PATH = os.path.join(APP_DIR, "controller.py")
LOCK_PATH = os.path.join(PROJECT_DIR, "controller.lock")
LOG_PATH = os.path.join(PROJECT_DIR, "controller.log")

DEFAULT_CONFIG = {
    "zcode_path": r"C:\Study\Zcode\ZCode.exe",
    "wallpaper": "assets/demo_wallpaper.png",  # 相对项目根；首次使用即带演示壁纸
    "port": 9333,             # CDP 调试端口
    "image_port": 18765,      # 本地图片服务端口
    "mode": "cover",          # cover | contain | fill | tile
    "position": "center",
    "repeat": "no-repeat",
    "darken": 0.25,           # 0~0.9
    "transparent_selectors": [".bg-background-win-alt"],
    "background_overrides": {
        # 主卡片：把 --color-background 变量变半透明 → 所有嵌套 bg-background 页面容器自动透出
        "section.bg-background.rounded-xl": "--color-background: rgba(22, 22, 22, 0.55)",
        # 显式兜底（个别容器可能带内联变量覆盖继承）
        "div.bg-background.rounded-xl": "rgba(22, 22, 22, 0.55)",
        "main#automations-main-toast-anchor": "rgba(22, 22, 22, 0.55)",
    },
}


def resolve_path(p):
    """绝对路径直接返回；相对路径按项目根解析。"""
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.join(PROJECT_DIR, p)


def load_config():
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    cfg = dict(DEFAULT_CONFIG)
    for k, v in data.items():
        if v is not None and v != "":
            cfg[k] = v
    # 相对路径字段按项目根解析
    for key in ("wallpaper", "zcode_path"):
        if cfg.get(key):
            cfg[key] = resolve_path(cfg[key])
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
