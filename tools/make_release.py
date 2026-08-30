"""打包 DTUG IDE 发布物（支持「精简 / 完整 / 编译器插件」三变体）。

版本号单一来源：dtug_ide/version.py。

变体：
  * 精简版 (lite)  DTUG-IDE-<ver>-windows-x64.zip
        —— 源码 + 冻结 onedir（内置 Python，开箱即用），不含编译器。
           适合本机已装 MSYS2/MinGW/ucrt64 或 PATH 中有 g++/clang++ 的用户。
  * 完整版 (full)  DTUG-IDE-<ver>-windows-x64-full.zip
        —— 精简版 + 轻量 ucrt64 GCC（已剔除文档/证书），
           双击 DTUG-IDE.exe 即可编译运行，无需任何额外环境。
  * 编译器插件     DTUG-IDE-<ver>-gcc-ucrt64-addon.zip
        —— 仅 ucrt64 工具链，解压到精简版根目录即等价于完整版
           （包内路径 _internal/dtug_ide/resources/mingw64，与 full 一致；
             app 也会从应用根目录向上回退查找，落点不强制）。

编译器来源：tools/stage_compiler.py 把本机 D:\\MSYS2\\ucrt64 暂存为
release/_staging/mingw64（剔除 share/ etc）。该 ucrt64 已是无 python 的精简
GCC；app 在调用时会显式传入 --sysroot 使其脱离 MSYS2 也能编译。

三变体统一使用标准 zip（ZIP_DEFLATED + allowZip64），Windows 资源管理器
原生即可解压，无需任何第三方解压软件。deflate 压缩率低于 7z 的 LZMA2，但换来
通用性（实测 full/addon 约 340/130 MB，仍远小于未压体量）。

用法：
    py tools/make_release.py
前置：先运行 PyInstaller 生成 release/DTUG-IDE 冻结 onedir。
"""

from __future__ import annotations

import faulthandler
import hashlib
import importlib.util as _ilu
import os
import shutil
import sys
import traceback
import zipfile
from pathlib import Path

faulthandler.enable()

ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = ROOT / "release"

# 暂存的轻量编译器（由 tools/stage_compiler.py 生成）
STAGED_COMPILER = RELEASE_DIR / "_staging" / "mingw64"
# PyInstaller 冻结 onedir 输出
ONEDIR_BUILD = RELEASE_DIR / "DTUG-IDE"

# 版本单一来源
def _read_version() -> str:
    vp = ROOT / "dtug_ide" / "version.py"
    spec = _ilu.spec_from_file_location("dtug_ide_version", vp)
    mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return getattr(mod, "__version__", "2.0.0")


VERSION = _read_version()
LITE_NAME = f"DTUG-IDE-{VERSION}-windows-x64"
FULL_NAME = f"DTUG-IDE-{VERSION}-windows-x64-full"
ADDON_NAME = f"DTUG-IDE-{VERSION}-gcc-ucrt64-addon"

LITE_ZIP = RELEASE_DIR / f"{LITE_NAME}.zip"
FULL_ZIP = RELEASE_DIR / f"{FULL_NAME}.zip"
ADDON_ZIP = RELEASE_DIR / f"{ADDON_NAME}.zip"

# 源码打包包含
INCLUDE_DIRS = ["dtug_ide", "docs", "tests", "tools", "packaging"]
INCLUDE_FILES = [
    "编译.py",
    "README.md",
    "CHANGELOG.md",
    "LICENCE",
    "MANIFEST.in",
    "pyproject.toml",
    "requirements.txt",
    ".gitignore",
]

# 永不打包的目录
EXCLUDE_DIRS = {".git", "__pycache__", "build", "dist", "release", ".workbuddy",
                ".ruff_cache", ".pytest_cache", "legacy", ".venv", "venv"}
# 不随源码打包的编译器目录（精简版不含；完整版由暂存编译器单独放入）
EXCLUDE_SUBPATHS = {"dtug_ide/resources/mingw64"}


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return True
    rel = "/".join(rel_parts)
    return any(rel == sub or rel.startswith(sub + "/") for sub in EXCLUDE_SUBPATHS)


def _iter_source_files() -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                rel = p.relative_to(ROOT)
                if _is_excluded(rel.parts):
                    continue
                out.append((p, rel))
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.is_file():
            out.append((p, p.relative_to(ROOT)))
    return sorted(out)


def _copy_source(dist: Path) -> int:
    n = 0
    for src, rel in _iter_source_files():
        dst = dist / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


def _copy_onedir(dist: Path) -> None:
    """把 PyInstaller 冻结 onedir 合并进 dist（同名文件以 onedir 为准）。"""
    if not ONEDIR_BUILD.is_dir():
        raise RuntimeError(
            f"未找到冻结 onedir 构建：{ONEDIR_BUILD}\n"
            "请先运行：py -m PyInstaller packaging/DTUG-IDE.spec "
            "--distpath release --workpath build --noconfirm --clean"
        )
    for item in ONEDIR_BUILD.iterdir():
        dst = dist / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)


def _onerr_chmod(func, p, exc):  # noqa: ANN001
    """rmtree/remove 遇到只读文件（解压产物常见）时清只读位后重试。"""
    try:
        os.chmod(p, 0o777)
        func(p)
    except OSError:
        pass


def _safe_delete(src: Path) -> None:
    """尽力删除已存在的文件或目录（归档前清理上一轮产物，避免 .old_ 堆积）。

    直接删除而非改名；在沙箱环境下若被拦截则静默忽略，不阻断打包流程。
    """
    if not src.exists():
        return
    try:
        if src.is_dir():
            shutil.rmtree(src, onexc=_onerr_chmod)
        else:
            os.remove(src)
    except OSError:
        pass


def _zip_dir(folder: Path, zip_path: Path) -> None:
    """把 folder 及其内容压缩为 zip，顶层保留 folder 名（相对其父目录）。"""
    if zip_path.exists():
        _safe_delete(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(folder.parent).as_posix())


def _zip_dir_with_arc(folder: Path, arcname: str, zip_path: Path) -> None:
    """把 folder 内容压缩为 zip，内部统一放到 arcname/ 之下（不含 folder 自身名）。

    用于编译器插件：把暂存 mingw64 放到 _internal/dtug_ide/resources/mingw64/，
    解压到精简版根目录即等价于完整版（bundled_gpp 也会向上回退查找）。
    """
    if zip_path.exists():
        _safe_delete(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                rel = p.relative_to(folder).as_posix()
                zf.write(p, f"{arcname}/{rel}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_info(dist: Path, lines: str) -> None:
    (dist / "BUILD-INFO.txt").write_text(lines, encoding="utf-8")


def main() -> None:
    try:
        _main_impl()
    except Exception:
        traceback.print_exc()
        sys.stderr.flush()
        raise


def _main_impl() -> None:
    if not STAGED_COMPILER.is_dir():
        raise RuntimeError(
            f"未找到暂存编译器：{STAGED_COMPILER}\n"
            "请先运行：py tools/stage_compiler.py"
        )

    lite = RELEASE_DIR / LITE_NAME
    full = RELEASE_DIR / FULL_NAME

    # ---- 1) 精简版（源码 + 冻结 onedir，无编译器）----
    _safe_delete(lite)
    lite.mkdir(parents=True)
    n = _copy_source(lite)
    _copy_onedir(lite)
    _write_info(
        lite,
        f"DTUG IDE {VERSION}\n"
        f"Variant: lite (no bundled compiler)\n"
        f"Build: source + frozen onedir (Python 内置，开箱即用)\n"
        f"Compiler: 需本机 MSYS2/MinGW/ucrt64 或 PATH 中的 g++/clang++\n"
        f"Python: >=3.10 (frozen)\nLicense: MIT\n",
    )
    _zip_dir(lite, LITE_ZIP)
    print(f"[lite] staged {n} source files + onedir -> {LITE_ZIP}", flush=True)

    # ---- 2) 完整版（精简版 + 轻量 ucrt64 编译器）----
    _safe_delete(full)
    full.mkdir(parents=True)
    # 直接以精简版内容为基础
    for item in lite.iterdir():
        dst = full / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    print("[full] lite copied, adding compiler...", flush=True)
    # 冻结 onedir 中模块位于 _internal/dtug_ide，故内置编译器必须落在
    # _internal/dtug_ide/resources/mingw64（bundled_gpp 据此查找）。
    compiler_dst = full / "_internal" / "dtug_ide" / "resources" / "mingw64"
    shutil.copytree(STAGED_COMPILER, compiler_dst, dirs_exist_ok=True)
    print("[full] compiler copied, zipping...", flush=True)
    _write_info(
        full,
        f"DTUG IDE {VERSION}\n"
        f"Variant: full (bundled lightweight ucrt64 GCC)\n"
        f"Build: source + frozen onedir (Python 内置) + ucrt64 工具链\n"
        f"Compiler: 内置 ucrt64 g++，双击 DTUG-IDE.exe 即可编译运行\n"
        f"Python: >=3.10 (frozen)\nLicense: MIT\n",
    )
    _zip_dir(full, FULL_ZIP)
    print(f"[full] lite + ucrt64 -> {FULL_ZIP}", flush=True)
    # 归档完成，清理未压缩工作目录（精简版内容已并入 full；full 已归档）
    _safe_delete(lite)
    _safe_delete(full)

    # ---- 3) 编译器插件包（仅 ucrt64）----
    _zip_dir_with_arc(
        STAGED_COMPILER, "_internal/dtug_ide/resources/mingw64", ADDON_ZIP
    )
    print(f"[addon] ucrt64 only -> {ADDON_ZIP}", flush=True)

    # ---- 校验和 ----
    sums = []
    for art in (LITE_ZIP, FULL_ZIP, ADDON_ZIP):
        if art.is_file():
            sums.append(f"{_sha256(art)}  {art.name}\n")
    if sums:
        (RELEASE_DIR / "SHA256SUMS.txt").write_text("".join(sums), encoding="utf-8")
        print(f"[sha] wrote SHA256SUMS.txt ({len(sums)} artifacts)", flush=True)

    # 体积概览
    print("\n=== 产物体积 ===", flush=True)
    for art in (LITE_ZIP, FULL_ZIP, ADDON_ZIP):
        if art.is_file():
            mb = art.stat().st_size / (1024 * 1024)
            print(f"  {art.name}: {mb:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
