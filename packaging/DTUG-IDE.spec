# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import platform
import runpy


project_root = Path(SPECPATH).resolve().parent
version = runpy.run_path(project_root / "dtug_ide" / "version.py")["__version__"]
machine = platform.machine().lower()
architecture = "x64" if machine in {"amd64", "x86_64"} else machine.replace(" ", "-")
release_name = f"DTUG-IDE-{version}-windows-{architecture}"
entry_point = project_root / "编译.py"
icon_path = project_root / "packaging" / "dtug-ide.ico"
version_file = project_root / "packaging" / "version_info.txt"


# 资源目录（resources）默认只打包 mingw64 之外的文件。
# 1.1GB 的 mingw64 工具链单独打包为 full / addon 变体，避免精简版被拖成 1.6GB。
#
# 注意：PyInstaller 对「末级为带点文件名」的 data 目标（如 dtug_ide/resources/random.h）
# 会误当作目录，把源文件再嵌一层变成 random.h/random.h。因此这里把 dest 写成
# 明确的目录形式（带尾斜杠），让 PyInstaller 自动把源文件 basename 填入该目录。
import os as _os

resources_root = project_root / "dtug_ide" / "resources"
resource_datas = []
for p in sorted(resources_root.rglob("*")):
    if not p.is_file():
        continue
    rel = p.relative_to(resources_root)
    if rel.parts and rel.parts[0] == "mingw64":
        continue  # mingw 单独处理（见 tools/make_release.py）
    # dest 目录（带尾斜杠），源文件 basename 由 PyInstaller 自动追加
    dest_dir = _os.path.join("dtug_ide", "resources", *rel.parent.parts)
    if not dest_dir.endswith(_os.sep):
        dest_dir += _os.sep
    resource_datas.append((str(p), dest_dir))

a = Analysis(
    [str(entry_point)],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(icon_path), "."),
        # 数据生成器依赖的 C++11 单头库 random.h（合并自 Data-Generator）
        *resource_datas,
    ],
    hiddenimports=[
        "dtug_ide",
        "dtug_ide.embedded_login",
        "dtug_ide.theme",
        "dtug_ide.settings_dialog",
        "dtug_ide.formatter",
        "dtug_ide.interactive_run",
        "dtug_ide.data_generator",
        # 工具链管理（编译器发现 / C++ 标准检测；多处为函数内懒导入，需显式声明）
        "dtug_ide.toolchain",
        # 聊天面板（复制自 Chat-online，依赖 markdown 渲染第三方库）
        "dtug_ide.chat_client",
        "dtug_ide.chat_panel",
        "dtug_ide.chat_widgets",
        "dtug_ide.chat_group_manage",
        "markdown_it",
        "mdit_py_plugins.container",
        "mdit_py_plugins.dollarmath",
        "mdit_py_plugins.tasklists",
        "pygments",
        "pygments.lexers",
        "pygments.formatters",
        "pygments.util",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineQuick",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DTUG-IDE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=str(version_file),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DTUG-IDE",
)
