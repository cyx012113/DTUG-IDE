# Changelog

All notable changes to this project will be documented in this file.

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
