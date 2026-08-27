# DTUG IDE

> 基于 PyQt6 的纯桌面 C++ 集成开发环境，专攻 CPP 方向，内置沙盒评测（编译 + 运行 + 判题），轻量、离线可用，并复用主页账号体系。

DTUG IDE 是一个不依赖浏览器的本地 C++ 编程工具：编辑器带语法高亮，一键编译运行，内置 OJ 风格的评测引擎（超时 / 内存 / 运行错误判定），并通过主页 `auth-worker.cyx012113.top` 复用同一套账号。适用于洛谷等竞赛场景下的本地刷题、调试与自测。

## 特性

| 类别 | 说明 |
|---|---|
| 编辑器 | `QPlainTextEdit` + `QSyntaxHighlighter` 实现的 C++ 语法高亮，行号、自动缩进 |
| 编译运行 | 调用本地 `g++` 编译，独立临时目录运行，分离 stdout / stderr，正确解码中文 |
| 沙盒评测 | 独立 tempdir 隔离，进程树整体 kill，支持 TLE / MLE / RE 判定 |
| 账号系统 | 复用主页账号（内置登录 / 内嵌网页登录 / 主页跳转 / 粘贴 token 四种方式） |
| 轻量 | 纯桌面客户端，无 WebUI、无后端常驻进程 |
| 跨发布 | 自带 PyInstaller 打包脚本，输出单文件夹（onedir）可执行包 |

## 目录结构

```
DTUG-IDE/
├── dtug_ide/              # 主程序包
│   ├── __init__.py
│   ├── __main__.py        # 模块入口（含崩溃日志、Qt 插件路径兜底、WebEngine 资源路径）
│   ├── main_window.py     # 主窗口与 UI 布局、账号状态联动
│   ├── editor.py          # C++ 语法高亮编辑器
│   ├── compiler.py        # 编译与运行逻辑
│   ├── judge.py           # OJ 评测引擎
│   ├── auth.py            # 账号客户端（auth-worker API + 主页跳转常量）
│   ├── login_dialog.py    # 登录对话框（内置 / 主页跳转 / 粘贴 token）
│   ├── embedded_login.py  # 内嵌网页登录（QWebEngineView + token 回调）
│   ├── utils.py           # 工具函数
│   └── version.py         # 版本信息
├── tests/                 # 单元测试
├── docs/images/           # 文档与图标（dtug-ide.ico）
├── packaging/             # 打包配置（DTUG-IDE.spec、version_info.txt、THIRD_PARTY_NOTICES.md）
├── tools/                 # 开发工具（make_release.py、collect_release_licenses.py）
├── 编译.py                # 兼容启动器（入口脚本，勿删）
├── legacy/                # 旧 Web 版参考代码（DTUG_IDE-web.exe、app.py、bug_report.md 等）
├── requirements.txt       # 运行依赖
├── pyproject.toml         # 包元数据与构建配置
├── MANIFEST.in           # 源码分发包含项
├── LICENCE                # 许可证（沿用 Chat-online 项目）
├── CHANGELOG.md           # 变更日志
└── .gitignore
```

## 环境要求

- Windows 10 / 11（x64）
- Python 3.10+（开发态）
- 本地已安装 `g++`（MinGW-w64 或 MSVC 工具链），并在 `PATH` 中
- 运行态独立 exe 无需上述环境

## 快速开始（源码运行）

```powershell
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m dtug_ide
```

## 打包为独立可执行文件（Windows，单文件夹）

```powershell
py -m pip install build pyinstaller
py -m PyInstaller packaging/DTUG-IDE.spec --distpath release --workpath build --noconfirm
```

构建产物为单文件夹（onedir）形式：`release/DTUG-IDE/`，其中 `DTUG-IDE.exe` 为入口，内置 Python 与 PyQt6（含 WebEngine），无需安装运行环境。随后可用 `py tools/make_release.py` 生成带校验和的发布 zip。

> 说明：本项目采用 **单文件夹（onedir）** 而非单文件（onefile），以避免临时解压带来的启动延迟与权限问题。

## 账号系统

DTUG IDE 复用主页账号体系（`auth-worker.cyx012113.top`），与网站、werewolf 等子站共用同一套账号。登录为可选功能，未登录也可正常使用全部编辑 / 编译 / 评测能力。

| 方式 | 说明 |
|---|---|
| 内置账号密码（无 CORS 问题） | 走 Python `urllib` 直连 auth-worker，不经过浏览器，不受 CORS 限制 |
| 🪟 内嵌网页登录（推荐） | `QWebEngineView` 内嵌主页登录页，登录后主页把 token 附加到本机回调 URL，桌面端拦截提取并校验 |
| 🌐 主页跳转登录 | 照搬 werewolf，按钮打开 `https://cyx012113.top/#login` 在主站完成登录 |
| 粘贴 token 绑定 | 已在主站登录的用户，从浏览器控制台取 `localStorage.auth_token` 粘贴回桌面端 |

- 工具栏右侧「🔐 登录」按钮打开登录 / 注册对话框。
- 支持「记住我」：勾选后 token 持久化到 `QSettings`（Windows 写入注册表 `HKEY_CURRENT_USER\Software\DTUG\IDE`），下次启动自动校验并恢复登录态。
- 已登录时按钮显示「👤 用户名」，点击展开菜单可「退出登录」。
- 状态栏右侧实时显示登录状态（未登录 / 已登录：用户名 [身份标签]）。
- 网络请求在后台线程执行，不阻塞界面；失败仅提示错误，不影响编辑器正常使用。

> 说明：token 以明文形式存于本机 `QSettings`（与网站端存于 `localStorage` / 根域 cookie 思路一致）。

## 主页登录回调原理

内嵌登录依赖主页的一处改动：登录成功后跳转时，若 redirect 目标不是 `*.cyx012113.top` 子站（例如本机 `http://127.0.0.1:8731/callback`），主页会把 `localStorage.auth_token` 以 `?token=` 附加到跳转 URL。桌面端通过 `QWebEnginePage.acceptNavigationRequest` 拦截该回调导航，提取 token 后后台调用 `/me` 校验，校验通过即完成登录。**无需本地 HTTP 服务器**。`*.cyx012113.top` 子站（如 werewolf）仍走原有 cookie SSO，不受影响。

## 技术栈

- **界面**：PyQt6（`QtWidgets` / `QtWebEngineWidgets`）
- **评测**：`subprocess` + `psutil` 进程树管理，独立临时沙盒目录
- **账号**：`urllib` 标准库 HTTP 客户端，主页 `auth-worker.cyx012113.top`
- **打包**：PyInstaller（onedir）

## 许可证

本项目沿用 [Chat-online](https://github.com/cyx012113) 项目的 `LICENCE`，详见仓库根目录 `LICENCE` 文件。

## 参考

- 项目结构参考同目录下其他项目（如 Chat-online）。
- 旧 Web 版实现保留于 `legacy/` 目录，仅供功能对比与迁移参考。
