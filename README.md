# DTUG IDE

[MIT License](https://mit-license.org/)

> 基于 PyQt6 的纯桌面 C++ 集成开发环境，专攻 CPP 方向，内置沙盒评测（编译 + 运行 + 判题），轻量、离线可用，并复用主页账号体系。

DTUG IDE 是一个不依赖浏览器的本地 C++ 编程工具：编辑器带语法高亮，一键编译运行，内置 OJ 风格的评测引擎（超时 / 内存 / 运行错误判定），并通过主页 `auth-worker.cyx012113.top` 复用同一套账号。适用于洛谷等竞赛场景下的本地刷题、调试与自测。

## 特性

| 类别     | 说明                                                                        |
| -------- | --------------------------------------------------------------------------- |
| 编辑器   | `QPlainTextEdit` + `QSyntaxHighlighter` 实现的 C++ 语法高亮，行号、自动缩进 |
| 编译运行 | 调用本地 `g++` 编译，独立临时目录运行，分离 stdout / stderr，正确解码中文   |
| 沙盒评测 | 独立 tempdir 隔离，进程树整体 kill；支持 TLE / MLE / RE，且可**终止 / 暂停 / 继续**；输出比较去行末空格、去末尾空行，可选「忽略大小写」 |
| 账号系统 | 复用主页账号（浏览器登录 / 内嵌网页登录 / 粘贴 token；均经主页 Cloudflare 人机验证） |
| 轻量     | 纯桌面客户端，无 WebUI、无后端常驻进程                                      |
| 跨发布   | 自带 PyInstaller 打包脚本，输出单文件夹（onedir）可执行包                   |
| 交互调试 | 「交互运行」边运行边输入（stdin 实时回传、输出流式显示），替代黑窗控制台    |
| 数据生成 | 合并 Data-Generator 的 `random.h` 单头库，可视化生成多组测试数据并一键导入  |
| 代码格式化 | 内置 C++ 轻量格式化器（4 空格缩进、关键字补空格、去尾随空格），`clang-format` 优先 |
| 编译指令 | Dev-C++ 风格编译 / 连接器指令设置（C++ 标准、编译标志、连接标志）持久化    |

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
│   ├── login_dialog.py    # 登录对话框（浏览器 / 内嵌网页 / 粘贴 token，均无凭据输入框）
│   ├── embedded_login.py  # 内嵌网页登录（QWebEngineView + token 回调）
│   ├── theme.py           # 主题与字体统一管理（UI / 编辑器调色板 + QSS 生成）
│   ├── settings_dialog.py # 设置对话框（界面主题 / 编辑器配色 / 字体 / 字号 / 编译选项）
│   ├── formatter.py       # 轻量级 C++ 代码格式化器（优先 clang-format）
│   ├── interactive_run.py # 交互式运行对话框（动态交互式调试）
│   ├── data_generator.py  # 数据生成面板（合并 Data-Generator 的 random.h）
│   ├── resources/         # 打包资源（random.h 单头库）
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

| 方式                         | 说明                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------- |
| 🪟 内嵌网页登录（推荐）      | `QWebEngineView` 内嵌主页登录页，登录后主页把 token 附加到本机回调 URL，桌面端拦截提取并校验 |
| 🌐 在浏览器中登录            | 按钮打开带 `?redirect=...` 的主页登录页；登录成功后主页把 token 附加到 `http://127.0.0.1:8731/callback?token=...`，桌面端**本地回调服务器**捕获并自动完成绑定（系统浏览器也能用 redirect 自动登录） |
| 粘贴 token 绑定              | 已在主站登录的用户，从浏览器控制台取 `localStorage.auth_token` 粘贴回桌面端                  |

> 重要：出于安全，桌面端**不内置用户名 / 密码 / 邮箱输入框**——主页登录接口（`auth-worker.cyx012113.top`）**强制要求 Cloudflare Turnstile 人机验证**，凭据只在真实浏览器环境中输入并通过验证后才能拿到 token。登录对话框仅提供上述三种经主页 CF 验证的桥接方式。

- 工具栏右侧「🔐 登录」按钮打开登录 / 注册对话框。
- 支持「记住我」：勾选后 token 持久化到 `QSettings`（Windows 写入注册表 `HKEY_CURRENT_USER\Software\DTUG\IDE`），下次启动自动校验并恢复登录态。
- 已登录时按钮显示「👤 用户名」，点击展开菜单可「退出登录」。
- 状态栏右侧实时显示登录状态（未登录 / 已登录：用户名 [身份标签]）。
- 网络请求在后台线程执行，不阻塞界面；失败仅提示错误，不影响编辑器正常使用。

> 说明：token 以明文形式存于本机 `QSettings`（与网站端存于 `localStorage` / 根域 cookie 思路一致）。

## 主页登录回调原理

内嵌登录依赖主页的一处改动：登录成功后跳转时，若 redirect 目标不是 `*.cyx012113.top` 子站（例如本机 `http://127.0.0.1:8731/callback`），主页会把 `localStorage.auth_token` 以 `?token=` 附加到跳转 URL。桌面端有两种捕获路径，二者互斥、互不干扰：

- **内嵌网页路径**：`QWebEnginePage.acceptNavigationRequest` 拦截该回调导航（不真正发起请求），提取 token 后后台调用 `/me` 校验，校验通过即完成登录。**无需本地 HTTP 服务器**。
- **系统浏览器路径**：对话框启动一个本地回调服务器（`_CallbackServer`，监听 `127.0.0.1:8731`），系统浏览器真实导航到回调 URL 时被服务器捕获 token，同样经 `/me` 校验后完成登录。

`*.cyx012113.top` 子站（如 werewolf）仍走原有 cookie SSO，不受影响。

## 外观与编辑器设置

通过菜单 **设置 (&T) → 外观与编辑器…** 可实时调整并持久化（写入 `QSettings`）：

| 选项         | 说明                                                                         |
| ------------ | ---------------------------------------------------------------------------- |
| 界面主题     | 暗色 / 亮色，整套 UI 配色随切换即时生效                                       |
| 字体         | 自动收集系统等宽字体（JetBrains Mono / Fira Code / Cascadia Code / Consolas 等） |
| 字号         | 8–40，支持 `Ctrl + 滚轮` 在编辑器中临时缩放                                  |
| 配色主题     | 编辑器语法高亮配色：暗色 · Dracula / 暗色 · Monokai / 暗色 · One Dark / 亮色 · GitHub |

- 所有颜色集中在 `dtug_ide/theme.py` 的 `UI_THEMES` / `EDITOR_THEMES` 调色板，新增主题只需追加条目。
- 修改后已打开的各个代码标签页会同步套用新字体与配色，无需重启。

## 内嵌登录页说明（人机验证）

内嵌网页登录使用 `QWebEngineView` 加载主页登录页。桌面端默认 QtWebEngine 的 User-Agent 含 `QtWebEngine` 字样，会被 Cloudflare 的浏览器完整性检查与 Turnstile 人机验证拦截，表现为「页面打不开 / 人机验证失败」。DTUG IDE 已在内嵌页面覆写 `userAgentForUrl` 为常规桌面 Chrome UA，并显式开启 JavaScript / LocalStorage / WebGL，使其通过校验。若仍遇到加载异常，内嵌登录框提供「刷新」与「🌐 在浏览器中打开」回退按钮。

## 编译 / 连接指令设置（Dev-C++ 风格）

通过菜单 **设置 (&T) → 外观与编辑器… → 编译选项** 配置，持久化到 `QSettings` 并实时生效：

| 选项         | 说明                                                                |
| ------------ | ------------------------------------------------------------------- |
| C++ 标准     | `c++11` / `c++14` / `c++17`（默认）/ `c++20` / `c++23`             |
| 编译指令     | 传给 `g++` 的编译标志，如 `-O2 -Wall -Wextra`                      |
| 连接器指令   | 连接阶段标志，如 `-static -lm`；置于命令行末尾，符合 `g++`/`ld` 惯例 |

- 命令行顺序：源文件 → 输出 → 编译指令 → `-std` → 连接指令。
- 编辑器工具栏的「编译器」下拉框仍用于选择 `g++` 可执行文件（命令本身）。

## 代码格式化

菜单 **编辑 (&E) → 格式化代码 (&F)**（或 `Ctrl+Shift+F`、工具栏 🎨 格式化）对当前文件排版：

- 4 空格缩进，依据 `{` `}` 自动计算层级（正确跳过字符串 / 字符 / 注释内的括号）；
- 去除行尾空白、压缩连续空行、保证文件以单个换行结尾；
- 控制关键字（`if`/`for`/`while`/`switch`/`catch`/`return`）后紧跟 `(` 时补一个空格；
- 若系统已安装 `clang-format` 则优先委托它，否则使用上述内置实现。

格式化只做「安全」排版，不改写语义，避免破坏竞赛代码；光标位置尽量保留。

## 交互式运行（动态交互式调试）

菜单 **运行 (&R) → 交互运行 (&I)**（或 `Ctrl+Shift+R`、工具栏 🐞 交互运行）：

- 编译后在独立进程中运行程序，输出实时流式显示到对话框；
- 在输入框回车即把该行作为 stdin 发送给程序，实现「边运行边交互」；
- 支持随时「■ 停止」与退出状态提示；子进程以无窗口方式启动，不再弹出黑窗。

## 数据生成（合并自 Data-Generator）

菜单 **运行 (&R) → 数据生成 (&G)**（或工具栏 🎲 数据生成）打开生成面板：

- 左侧编写生成器（默认模板基于 Data-Generator 的 `pg::Random` 单头库 `random.h`，随 IDE 打包；也可用任何向 stdout 输出数据的程序）；
- 右侧可选填写「标程」，生成器产出 `.in` 后由标程运行产出对应的 `.out`；
- 设定组数，点「🎲 生成数据」即可生成多组样例（复用沙盒执行，无黑窗、带时限）；
- 点「⬇ 导入为测试用例」把生成的 `(输入, 期望输出)` 一键加入当前文件的评测用例。

> `random.h` 来自隔壁项目 [Data-Generator](https://github.com/cyx012113/Data-Generator)（C++11 单头库，MIT）。

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
