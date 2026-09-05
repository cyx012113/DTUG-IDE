# Changelog

All notable changes to this project will be documented in this file.

## 3.0.2 (2026-09-05)

### 新增（编辑器增强）
- 空括号对一键删除：光标位于空 `()`/`[]`/`{}` 中间时，按 Backspace / Delete 直接删掉整对括号。
- `{}` 回车自动展开：在空花括号中间按回车，自动展开为两行并把光标落在中间行、自动缩进。
- 括号包裹选区：框选代码后按 `(`/`[`/`{`，用对应闭合符把选中内容包起来。
- `#define int long long` 自动改 `signed main`：检测到该宏定义后，自动把后续的 `int main(` 替换为 `signed main(`（注释行跳过）。
- 括号配对高亮 + 失配高亮：正向词法扫描（跳过字符串 / 字符 / `//` / `/* */` 注释）建立括号映射，鼠标悬停在括号旁时高亮两对括号；括号不匹配时红色高亮。
- 拖动移动代码、Ctrl + 拖动复制代码。
- 框选后 Tab 整体缩进、Shift + Tab 整体去除一个缩进。

### 修复
- 修复「不能一次性删掉一对括号和一个缩进」：退格 / 删除现可整对删括号、整层删缩进。
- 修复「缩进处理 bug」：统一缩进单位为 4 空格，回退 / 自动缩进均按此计算。
- 修复「登录获取 token 时界面卡住」：聊天会话 / 私聊列表拉取改为后台线程异步执行，不再阻塞主线程。
- 修复「聊天框背景白色、与主题不同步」：原误用只对 `QTreeWidget` / `QGroupBox` 生效的 `tree_qss` / `group_qss`，新增针对 `QListWidget` / `QLineEdit` 的 `chat_qss`。
- 修复「聊天消息加载错乱」：轮询拉到消息后校验当前房间，切房后丢弃过期响应。
- 修复「内置编译器需手动设全局环境变量才能找到 DLL」：新增 `bin_dir()` / `runtime_env()`，把编译器 `bin` 目录注入子进程 `PATH`，编译 / 运行 / 交互运行均无需手动配置。

## 3.0.1 (2026-08-30)

### 修复
- 修复「聊天消息里的表情显示成很窄的一小条」：应用字体原只声明单一族 `Segoe UI`，Qt 富文本引擎渲染 emoji 时分配了错误的水平步进宽度。改为用 `QFont.setFamilies(...)` 带 emoji 回退族（`Segoe UI Emoji` / `Noto Color Emoji` / `Apple Color Emoji`），emoji 在消息气泡、表情选择面板、输入框中均按正确比例渲染。
- 修复「切换主题后有些按钮仍是黑色」：`_apply_theme` 重新着色时漏掉了暂停/继续/停止/格式化/数据生成五个主窗口按钮；且聊天面板 `ChatDock` 原本没有 `apply_theme` 方法，其私聊/建群/加群/群管理/发送/表情等按钮永远不会随主题刷新。现已补齐主窗口五按钮的重着色，并为 `ChatDock` 新增 `apply_theme()` 在主题切换时主动刷新；评测结果条 `judge_summary` 的写死深色背景也改为随主题的 `selection` 色。
- 修复「切换成纯白主题后，编辑器主题名称仍未更改」：界面主题与编辑器配色主题原本完全独立。现在切换界面主题时，若当前编辑器配色主题与界面明暗不一致，自动同步到对应模式（亮色界面 → `亮色 · GitHub`，暗色界面 → `暗色 · Dracula`），设置对话框里的「配色主题」名称会随之更新。加载已保存设置时屏蔽信号，不会覆盖用户原有偏好。
- 修复「编译器选项混在外观和编辑器里」：设置对话框重构为「外观 / 编辑器 / 编译器」三个独立标签页，编译器（C++ 工具链）相关选项完全独立成「编译器」标签页，不再与外观、编辑器混排。
- 修复「编译器发现硬编码 `D:\MSYS2`」：`toolchain._mingw_roots` 不再硬编码用户特定的 `D:\MSYS2\mingw64` / `D:\MSYS2\ucrt64`，改为：① 标准安装位置（C: 盘常见路径）兜底；② 优先扫描 `PATH` 环境变量（含同级 `mingw64`/`ucrt64`/`clang64` 等环境）动态发现；③ 设置里可「浏览…」手动导入任意 `g++.exe`。新增「扫描环境变量」按钮，随时刷新编译器列表。
- 修复「启动即崩溃 `RuntimeError: wrapped C/C++ object of type QPushButton has been deleted`」：3.0.1 为 `ChatDock` 新增 `apply_theme()` 时，误把登录/未登录切换栈的装配代码（`QStackedWidget`、`setWidget(central)`、`setMinimumWidth`）写进了 `apply_theme()` 而非 `_build_ui()`，导致构建期 `central` 控件树没有任何存活引用、`__init__` 结束后被垃圾回收、子按钮（含 `_dm_btn`）随之销毁，随后 `_connect_signals()` 连接已死按钮即抛异常。已将该段装配代码移回 `_build_ui()`，`apply_theme()` 仅保留样式刷新。版本号维持 `3.0.1` 不变。

## 3.0.0 (2026-08-28)

### 新增（聊天系统全面升级，复用 Chat-online / 网站前端同源 chat-worker 后端）
- **私聊云端持久化**：私聊会话列表不再只存内存，登录时从 `chat-worker` 的 `/dms/mine` 拉取、新建私聊时写入 `/dms`，刷新或换设备都不会丢；首次为空时按历史 `dm:` 消息自动回填。
- **表情包系统**：输入框新增「😊」按钮，弹出 emoji 选择面板（72 个常用表情），点击插入到光标处，随主题（含暗色）变色。
- **群号可见（群主 / 管理员）**：群管理面板顶部显示群号并支持「复制」；会话列表中群聊项对群主 / 管理员显示 `群号：<id>` 悬停提示，普通成员不可见。
- **右键撤回 / 删除**：消息区支持右键自定义菜单——本人可撤回自己的消息，群主 / 管理员可删除群内任意消息；删除成功后气泡即时从界面移除。
- **群权限管理全套**：新增群管理对话框 `chat_group_manage.py`，覆盖——成员列表（踢人 / 禁言 / 设管理员 / 转让）、加入方式（自由加入 / 需审核 / 禁止加入）、是否允许成员拉人、全员禁言（不对群主 / 管理员生效）、查看入群申请并审批、解散群。权限边界与后端 `getGroupActor` 完全一致：群主 > 管理员 > 普通成员，群主 = 超级管理员。
- **申请进群**：新增「加群」按钮，输入群号即可按该群设置（自由 / 审核 / 禁止）加入或提交申请。
- **群 ID 改为递增数字**：新群自动获得 `1, 2, 3…` 的递增数字 ID（后端 `group_seq` 计数器原子取号，无并发竞态），更直观好记；旧随机串群 ID 不受影响。

### 修复
- 修复「刷新后私聊从列表消失」：与网站同源问题，改为云端持久化（见上方私聊云端持久化）。
- 修复「建群后其它群从列表消失」：建群不再 emit 单条会话更新导致整表被替换，改为建群后主动重新拉取完整群列表。

### 注意
- 上述聊天功能依赖 `chat-worker` 新接口（`/dms/mine`、`/dms`、`/groups/*` 全套、`/messages/delete`、`group_seq`、`user_dm_rooms` 等）。**需先将 `chat-worker` 部署上线并跑 `chat-migrate-v1.sql` 迁移**，桌面端这些功能才能联调生效。

## 2.4.0 (2026-08-27)

### 新增（Dev-C++ 风格编译运行体验）
- **快捷键**：`Ctrl+Shift+A` 格式化代码；`F9` 编译；`F10` 运行；`F11` 编译并运行。默认「外部黑窗」运行（`start cmd /c` 新开控制台，`chcp 65001` 切 UTF-8 代码页，保证中文正常显示）。
- **内置 C++ 工具链**：从 `D:\MSYS2\mingw64` 复制一份最新 MinGW64（GCC 16.2.0，支持到 C++26）作为内置工具链，置于 `dtug_ide/resources/mingw64`；用户也可在设置里手动指定自己的 g++.exe。
- **C++ 标准自动识别**：对任意编译器，先用已知标准列表（`c++98/03/11/14/17/20/23/26`）用 `-std=c++{id}` 编译空脚本探测，若最高已知标准受支持再用二分法向上探测（c++27…c++200），生成可选版本下拉列表。实测内置 GCC 16.2.0 支持 `c++98/11/14/17/20/23/26`。
- **自定义编译指令**：设置里可填写编译/连接指令，默认 `-static-libgcc -O2`（实际命令为 `-static-libgcc -O2 -std=c++14`，`-std` 由标准下拉单独控制）。
- **默认代码模板**：改为 `#include <bits/stdc++.h>\nusing namespace std;\nsigned main() {\n    return 0;\n}`。

### 修复 / 优化
- **修复中文输出爆掉（编码）**：编译时追加 `-finput-charset=UTF-8 -fexec-charset=UTF-8`；捕获模式输出用 `_decode_bytes()` 按 `utf-8→gbk→latin-1→utf-8(replace)` 回退解码，避免中文或混合编码导致崩溃/乱码。
- **修复「内置工具链在中文路径下链接失败」（关键根因）**：mingw-w64 的 `ld.exe` 无法打开路径中含非 ASCII 字符（如中文目录名）的文件，导致项目放在中文路径（如 `D:\Github仓库\...`）时链接报 `cannot find crt2.o / libstdc++ / libgcc` 等。`toolchain.resolve_ascii_gpp()` 在「实际编译链接」时把整份工具链复制（并解析符号链接为真实文件）到纯 ASCII 中转目录（如 `C:\Users\<user>\AppData\Local\Temp\dtug_ide_toolchain\mingw64`，末级须为 `mingw64` 以保持可重定位标记），按源 mtime 缓存复用，避免每次复制 1GB+ 文件。**版本探测用 `-c` 仅编译不链接，中文路径下本就能工作，无需中转。**
- **修复内置工具链符号链接断链**：`D:\MSYS2\mingw64` 中的 `lib/libgcc.a`、`lib/gcc/.../default-manifest.o` 等为符号链接，直接 `robocopy` 会复制成指向源机的断链。`copy_toolchain()` 与中转复制均改为解析符号链接为真实文件内容。

### 2.3.3 (2026-08-27)

### 新增
- **评测终止 / 暂停 / 继续**：评测控制台新增「⏸ 暂停」「▶ 继续」「⏹ 停止」按钮。`JudgeEngine.judge()` 引入 `JudgeControl`（stop / pause 标志），在每用例之间检查；`JudgeWorker` 提供 `request_stop / request_pause / request_resume`，停止时调用 `Compiler.kill_active()` 立即终止正在运行的子进程。被「停止」截断的用例在表格中标为「已停止」，并在汇总行提示「已停止（AC x/y，共 z）」。
- **评测比较「忽略大小写」模式**：评测控制台新增「忽略大小写」复选框，勾选后比较输出时整体转小写后再判等。
- **系统浏览器也能用 redirect 自动登录**：`EmbeddedLoginDialog` / `LoginDialog` 现启动本地回调服务器（`_CallbackServer`，监听 `127.0.0.1:8731`），「在浏览器中用主页账号登录」按钮打开带 `?redirect=...` 的主页，登录成功后主页把 token 附加到 `http://127.0.0.1:8731/callback?token=...`，本地服务器捕获并自动完成绑定（内嵌网页路径仍由 QWebEngine 拦截导航，两条路径互斥、互不干扰）。

### 优化
- **优化评测输出比较**：统一规范化——逐行去除行末空格、去除末尾空行（始终生效），再与期望输出比对；不再因行尾多余空格或末尾空行误判 WA。
- **移除登录框的用户名 / 密码 / 邮箱输入框**：出于安全，登录由主页 Cloudflare Turnstile 人机验证保护，凭据只在浏览器中输入，桌面端不保存密码。登录对话框仅保留「浏览器登录」「内嵌网页登录」与「粘贴 token 绑定」三种经主页 CF 验证的桥接方式。

### 修复
- 修复「登录成功仍无法自动获取 token」：根因是内嵌登录页构造的 URL 把 `#login` 片段放在 `?redirect=` 查询串之前（`HOME_LOGIN_URL` 本身已含 `#login`），导致 `?redirect=` 整段被吞进 URL 片段，`window.location.search` 为空、主页读不到 `redirect`，自然不会把 token 附加到回调。改为 `https://cyx012113.top/?redirect=<回调>#login`（查询串在前、片段在后），主页 `appendTokenToExternalRedirect` 现能正确把 `localStorage` 中的 token 附加到 `http://127.0.0.1:8731/callback?token=...`，内嵌页 `acceptNavigationRequest` 随之截获并校验登录。（上版本仅修了 `quote` 编码方向，未触达此顺序问题。）
- 修复「评测结果无法依次显示」：原先 `JudgeWorker` 把整个 `judge()` 跑完后才在 `finished` 信号里一次性重绘表格，所有用例结果挤在最后一起出现。现给 `JudgeEngine.judge()` 增加每用例完成回调 `on_result(result, idx)`，`JudgeWorker` 通过新增的 `result_ready` 信号跨线程发出，UI 的 `_on_case_done` 增量刷新对应行（状态/用时/内存/配色/悬浮提示）并实时更新「评测中... (i/N)」，实现逐用例顺序显示。

## 2.3.1 (2026-08-27)

### 修复
- 修复内置 C++ 格式化器缩进错乱：原实现对「行首连续 `}`」既按 `lead_close` 预减一级缩进、又按本行净括号数 `delta` 再减一次，导致每个 `}` 多减一级，嵌套代码（如 `while { }` 之后的 `return`、结构体方法体内的返回语句）塌缩到行首。改为「本行按 `indent - lead_close` 打印，`indent += delta` 只调整一次」。已通过幂等性校验（`format(format(x)) == format(x)`）与括号配平校验。
- 增强格式化器的空格规范化（仍仅改空白、不改语义）：关键字补空格列表新增 `else` / `do`；`for (a; b; c)` 三段分号按 `; ` 规范化，并用括号配平扫描支持头内嵌套括号（如 `for (...; i < __lg(n); ...)`）。直接回应「空格不对的代码有时不格式化」。

## 2.3.0 (2026-08-27)

### 新增
- **Dev-C++ 风格编译 / 连接指令设置**：设置对话框新增「编译选项」页（C++ 标准、编译指令、连接器指令），持久化到 `QSettings` 并实时生效；命令行顺序为 源文件 → 输出 → 编译指令 → `-std` → 连接指令。
- **代码格式化器**（模仿 Prettier 的「保存即格式化」理念）：新增 `dtug_ide/formatter.py`，4 空格缩进、关键字补空格、去尾随空格、压缩空行、保证单换行结尾；环境有 `clang-format` 时优先委托。菜单/工具栏 `格式化代码`（`Ctrl+Shift+F`），保留光标位置。
- **交互式运行（动态交互式调试）**：新增 `dtug_ide/interactive_run.py`，编译后以管道运行程序，输出流式显示、输入行回车即发 stdin，支持随时停止。
- **合并 Data-Generator 数据生成能力**：打包其 `random.h` C++11 单头库到 `dtug_ide/resources/`，新增 `dtug_ide/data_generator.py` 面板，基于 `pg::Random` 生成多组测试数据（可选标程产出 `.out`），一键导入为当前文件的评测用例。

### 修复
- 修复「改字体无效」：原先全局 `QWidget { font-family }` QSS 优先级高于 `setFont()` 锁死编辑器字体；改为由 `QApplication.setFont` 设置界面字体，编辑器/控制台等宽字体恢复可调。
- 修复标签栏在暗色模式下显示白色：强化 `tab_qss`（补充 `QTabBar` 整体背景与未选中/hover 配色），并在 `_apply_theme` 中重设标签栏样式。
- 修复编译 / 运行「一直弹出黑窗」：`compiler.compile()` 与 `run_sandboxed()` 的 `subprocess.Popen` 在 Windows 下加 `CREATE_NO_WINDOW`（0x08000000），不再弹出控制台黑窗（独立终端运行仍保留可见窗口）。
- 修复「登录成功后不会自动获取 token」：内嵌登录把回调 URL 过度编码（`quote(safe="")`），导致主页 `appendTokenToExternalRedirect` 的 `new URL()` 误判为同域而不附加 token；改为保留 `:` 与 `/`（`quote(safe=":/")`），登录成功后主页正常附加 `?token=` 并由桌面端拦截回调解码。

## 2.2.0 (2026-08-27)

### 新增
- 新增「设置 → 外观与编辑器」对话框，支持**界面主题（暗色 / 亮色）**、**编辑器配色主题（Dracula / Monokai / One Dark / GitHub 亮色）**、**字体族**与**字号**的实时调整与持久化。
- 新增 `dtug_ide/theme.py`：集中管理 UI / 编辑器调色板与 QSS 生成，所有控件颜色由主题驱动，做到「主题可调」。
- 新增 `dtug_ide/settings_dialog.py`：设置对话框，修改即时生效（已打开的编辑器同步套用），并持久化到 `QSettings`。
- 代码编辑器 `Ctrl + 滚轮` 临时缩放字号。

### 修复
- 修复内嵌网页登录页「打不开 / 人机验证失败」：内嵌 `QWebEngineView` 覆写 `userAgentForUrl` 为常规桌面 Chrome UA（去除 `QtWebEngine` 标记），并显式开启 JavaScript / LocalStorage / WebGL，绕过 Cloudflare 浏览器完整性检查与 Turnstile 拦截。
- 修复点击「内嵌网页登录」时懒导入 `PyQt6.QtWebEngineWidgets` 触发 `ImportError`：在 `__main__.py` 创建 `QApplication` 前设置 `Qt.AA_ShareOpenGLContexts`。
- 内嵌登录框新增「刷新」与「🌐 在浏览器中打开」回退按钮，并监听加载失败 / 渲染进程异常给出明确提示。
- 内置账号密码登录因服务端强制 Cloudflare 人机验证无法自行通过，失败提示改为引导使用内嵌 / 浏览器登录（而非晦涩报错）。

## 2.1.0 (2026-08-26)

### 新增
- 集成主页账号登录系统，复用 `auth-worker.cyx012113.top` 登录 API（与网站、werewolf 子站共用账号）。
- 新增 `dtug_ide/auth.py`：标准库 `urllib` 实现的认证客户端（登录 / 注册 / 取用户信息 / 修改资料），无第三方依赖。
- 新增 `dtug_ide/login_dialog.py`：登录 / 注册切换对话框，网络请求在后台线程执行，不阻塞 UI。
- 主窗口工具栏新增「🔐 登录」按钮，状态栏新增登录状态标签；登录后可展开菜单「退出登录」。
- 支持「记住我」：token 持久化到 `QSettings`，启动时自动校验并恢复登录态。
- 修复 Cloudflare 浏览器完整性检查拦截（默认 `Python-urllib` UA 触发 Error 1010），客户端统一携带浏览器 UA。

## 2.0.0 (2026-08-23)

### 重构
- 从 Flask Web UI 完全重构为 PyQt6 桌面客户端，无需浏览器、无需启动服务。
- 新增 C++ 语法高亮编辑器，支持关键字、类型、字符串、注释、多行注释高亮。
- 新增多标签页代码编辑，支持新建 / 打开 / 保存 / 另存为 / 关闭。
- 新增编译运行模块，支持一键编译运行，内嵌控制台输出或独立 CMD 终端。
- 新增 OJ 风格沙盒评测引擎，支持时间限制、内存限制、AC / WA / TLE / MLE / RE / CE 判定。
- 新增样例快速测试面板，支持即时输入 / 期望输出验证。
- 新增测试点批量导入，支持 `.in` / `.ans` / `.out` 文件夹导入。

### 修复
- 修复编译期 pipe 死锁（大量输出导致缓冲区满）。
- 修复编译超时后进程残留（递归 kill 进程树）。
- 修复 Windows 默认 cp936 解码 UTF-8 中文崩溃。
- 修复 `stderr` 混入 `stdout` 导致 WA 误判。
- 修复 Windows 文件锁导致 `.exe` 无法删除。
- 修复所有测试点共用工作目录导致文件状态污染。
- 修复路径含空格时 `shell=True` 传参错误。
- 修复时间限制可传入负数或极大值。

### 项目结构
- 模仿 `Chat-online` 项目结构，新增 `pyproject.toml`、`MANIFEST.in`、`CHANGELOG.md`、`LICENCE`、`.gitignore`。
- 新增 `tests/`、`docs/`、`packaging/`、`tools/` 目录。
- 新增 `编译.py` 兼容启动器。
