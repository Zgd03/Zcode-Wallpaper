# ZCode 壁纸启动器（Zcode-Wallpaper）

给 **ZCode 桌面客户端**（智谱 AI / Z.ai 的 Electron 开发应用）加**图片壁纸**：选一张图作为 ZCode 背景。

**核心原则：不修改 `app.asar`、不写入 ZCode 安装目录任何文件**，因此 **ZCode 升级后定制不会被覆盖**。

## 原理（为什么升级不丢）

- ZCode 是 Electron 应用，其主渲染页 `html, body, #root` 本身已是透明背景（`background: 0 0 !important`）。
- 本项目通过 **CDP（Chrome DevTools Protocol）** 注入：
  1. 启动器以 `--remote-debugging-port=9333` 启动 ZCode；
  2. 控制器连接 DevTools WebSocket，向页面注入 `app/inject/wallpaper.js`；
  3. 注入脚本在 `body` 下插入壁纸层 + 暗化层，并把 `#root` 提升到 `z-index:1`，再按配置把应用的背景容器改为透明/半透明，露出壁纸；
  4. 控制器常驻心跳，页面刷新自动重新注入，换壁纸无需重启。
- 壁纸图片由控制器内置的本地 HTTP 服务提供（`http://127.0.0.1:18765/wallpaper`），规避 `file://` 子资源限制。
- 所有定制文件都在本项目目录内（`app/`、`config.json`、`assets/`），ZCode 升级替换的是 `C:\Study\Zcode\*`，与本项目无关，因此天然不受影响。

## 目录结构

```
Zcode-Wallpaper/
├── app/                    # 应用代码（全部 Python + 注入脚本）
│   ├── __init__.py
│   ├── main.py             # 启动器 GUI / CLI
│   ├── controller.py       # CDP 控制器
│   ├── wsclient.py         # 最小 WebSocket 客户端
│   ├── config.py           # 共享配置
│   └── inject/wallpaper.js # 注入到渲染进程的壁纸脚本
├── assets/demo_wallpaper.png   # 演示壁纸
├── config.json             # 运行时配置（本机，不入库）
├── config.example.json     # 配置模板
├── run.bat                 # Windows 一键启动 GUI
└── README.md
```

## 使用

> 所有命令在**项目根目录**执行。

### 首次使用

```bash
# 若没有 config.json，复制模板（或直接运行 GUI，未配置时会用演示壁纸）
copy config.example.json config.json
```

### 方式一：GUI（推荐）

```bash
python app/main.py
# 或双击 run.bat
```

1. 点「浏览…」选择一张图片（PNG/JPG/BMP/GIF/WebP 均可）。
2. 确认 ZCode 程序路径已自动填入（`C:\Study\Zcode\ZCode.exe`）。
3. 调整「样式模式 / 图片位置 / 暗化」。
4. 点 **「启动/重启 ZCode（带壁纸）」**。若 ZCode 正在运行，会先结束再带壁纸启动。
5. 界面可随时关闭，壁纸保持生效（控制器独立常驻，日志 `controller.log`）。

> 注意：壁纸生效的前提是 ZCode 由本启动器启动（自动带调试端口）。直接双击 `ZCode.exe` 不会注入壁纸。

### 方式二：命令行

```bash
python app/main.py --cli launch      # 结束已运行的 ZCode → 带壁纸重启（前台阻塞运行）
python app/controller.py             # 仅启动控制器（附加/拉起 ZCode）
python app/controller.py --attach    # 只附加已开的调试实例，不启动 ZCode
python app/controller.py --probe     # 探测页面 DOM（用于调整透明化选择器）
python app/controller.py --shot x.png  # 注入后截图（调试用）
```

## 配置（config.json）

| 字段 | 说明 | 默认 |
|---|---|---|
| `zcode_path` | ZCode.exe 路径 | `C:\Study\Zcode\ZCode.exe` |
| `wallpaper` | 壁纸图片路径（相对项目根也行） | `assets/demo_wallpaper.png` |
| `port` | CDP 调试端口 | `9333` |
| `image_port` | 本地图片服务端口 | `18765` |
| `mode` | `cover` / `contain` / `fill` / `tile` | `cover` |
| `position` | 图片对齐 | `center` |
| `darken` | 0~0.9 暗化蒙层，保证文字可读 | `0.25` |
| `transparent_selectors` | 设为 `background:transparent!important` 的 CSS 选择器列表 | `[".bg-background-win-alt"]` |
| `background_overrides` | 高级：`选择器 → background 值`（如让主内容卡片半透明） | 见下 |

默认的“整窗壁纸 + 内容区半透明（含设置/自动化等各页面）”效果：

```json
{
  "transparent_selectors": [".bg-background-win-alt"],
  "background_overrides": {
    "section.bg-background.rounded-xl": "--color-background: rgba(22, 22, 22, 0.55)",
    "div.bg-background.rounded-xl": "rgba(22, 22, 22, 0.55)",
    "main#automations-main-toast-anchor": "rgba(22, 22, 22, 0.55)"
  }
}
```

- 第一条把主内容卡片的 `--color-background` 变量改为半透明，**所有嵌套使用 `bg-background` 的页面容器（设置、自动化等）都会自动透出壁纸**，无需逐页配置；后两条是显式兜底。
- `background_overrides` 的值若不含 `:` 则按 `background` 处理；若含 `:` 则按自定义 CSS 声明处理（多条用 `;` 分隔，各加 `!important`）。
- 想让壁纸只出现在侧栏/边框、内容区保持不透明：把 `background_overrides` 改为 `{}`。
- 这些选择器基于 ZCode 3.7.7 的内部类名；ZCode 升级若改了类名，相关规则静默失效（壁纸在透明处仍显示），不报错、不影响使用。也可用 `--probe` 探查新版本 DOM 后调整。

**运行中改 `config.json`（或 GUI 重新保存）会自动生效**（控制器每 3 秒检测一次），无需重启 ZCode。

> 说明：GUI 保存时**以磁盘上最新的 config.json 为基底合并**，只覆盖 GUI 里能改的字段，因此手工加进 `background_overrides` 等高级规则不会因 GUI 保存而丢失。控制器每隔几秒会**全量重注入**壁纸脚本，即使页面被旧配置污染也会自动恢复；注入脚本的 `apply()` 是**幂等**的（内容不变就不改动任何 DOM），因此周期性心跳不会造成闪屏。若更新了 `main.py`，请重启 GUI 再保存。

**控制器是单实例的**：通过 `controller.lock` 锁 + 固定图片端口绑定保证同时只有一个控制器运行；GUI 每次启动前会先清理旧的控制器进程。避免多控制器互相覆盖注入导致壁纸 URL 在多个端口间跳变而闪屏。

## 常见问题

- **壁纸没生效？** 确认 ZCode 是本启动器启动的（`controller.log` 有 `已注入 N 个页面`）；检查 `config.json` 的 `wallpaper` 路径存在。
- **端口被占用？** 改 `port`；或先 `taskkill /IM ZCode.exe /F /T` 再启动。
- **想恢复原样？** 结束 ZCode，用官方方式启动即可（本项目不写入 ZCode 任何文件）。

## 升级说明

ZCode 升级（自动更新或手动安装）会整体替换 `C:\Study\Zcode\*`，本项目的定制文件都在项目目录，升级后仍通过启动器注入壁纸，无需重装。若升级改变了渲染 DOM 结构，用 `--probe` 探查后调整 `transparent_selectors` / `background_overrides` 即可。

## 免责声明

本工具仅在本机对 ZCode 做视觉定制（壁纸），通过官方 DevTools 协议注入，不改动应用文件、不上传任何数据。
