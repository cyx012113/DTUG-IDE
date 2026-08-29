"""校验发布物里内置编译器的落点是否正确（bundled_gpp 能找到）。

用法：
    py tools/verify_release.py
前置：tools/make_release.py 已产出三变体。

校验内容：
  1. full 包解压后，_internal/dtug_ide/resources/mingw64/bin/g++.exe 存在，
     且直接加载冻结的 toolchain.py 调 bundled_gpp() 能返回该路径。
  2. lite 包 + addon 包解压到同一目录后，bundled_gpp() 也能找到编译器
     （验证 addon 落点 _internal/dtug_ide/resources/mingw64 与 walk-up 兜底）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import zipfile
import py7zr
from pathlib import Path

RELEASE = Path(__file__).resolve().parent.parent / "release"
import time as _time
VERIFY = RELEASE / "_verify" / str(int(_time.time()))
LITE_ZIP = RELEASE / "DTUG-IDE-3.0.0-windows-x64.zip"
FULL_7Z = RELEASE / "DTUG-IDE-3.0.0-windows-x64-full.7z"
ADDON_7Z = RELEASE / "DTUG-IDE-3.0.0-gcc-ucrt64-addon.7z"


def _load_toolchain(frozen_here: Path):
    """加载源码 toolchain.py，但把其 ``__file__`` 指向冻结位置（_internal/dtug_ide/toolchain.pyc），

    以复现运行时 bundled_gpp() 的真实解析（冻结模块在 PYZ 归档里，__file__ 为虚拟路径，
    其 .parent 即 _internal/dtug_ide）。这样 bundled_gpp() 查 ``here/resources/mingw64``
    就能命中磁盘上真实的编译器落点。
    """
    src = RELEASE.parent / "dtug_ide" / "toolchain.py"
    spec = importlib.util.spec_from_file_location("toolchain_check", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 模拟冻结态：__file__ 指向 _internal/dtug_ide/toolchain.pyc
    mod.__file__ = str(frozen_here / "toolchain.pyc")
    return mod


def _extract_zip(zipp: Path, dst: Path) -> None:
    # 每次使用全新目录（VERIFY 已带时间戳），直接解压，避免旧残留冲突。
    with zipfile.ZipFile(zipp) as zf:
        # 归档内顶层为 DTUG-IDE-3.0.0-windows-x64/，解开到 dst
        zf.extractall(dst)


def _extract_7z(arc: Path, dst: Path) -> None:
    with py7zr.SevenZipFile(arc, "r") as zf:
        zf.extractall(path=str(dst))


def _check(label: str, app_root: Path) -> bool:
    # app_root = 解压后的应用根目录（含 DTUG-IDE.exe 与 _internal/）
    frozen_here = app_root / "_internal" / "dtug_ide"
    mod = _load_toolchain(frozen_here)
    gpp = mod.bundled_gpp()
    ok = bool(gpp) and os.path.isfile(gpp)
    print(f"[{label}] bundled_gpp() -> {gpp!r}  {'OK' if ok else 'FAIL'}")
    return ok


def main() -> int:
    if not (LITE_ZIP.exists() and FULL_7Z.exists() and ADDON_7Z.exists()):
        print("缺少发布物，请先运行 make_release.py", file=sys.stderr)
        return 2

    # 1) full 包
    full_dir = VERIFY / "full"
    full_dir.mkdir(parents=True, exist_ok=True)
    print(">> 解压 full 包 ...", flush=True)
    _extract_7z(FULL_7Z, full_dir)
    full_ok = _check("full", full_dir / "DTUG-IDE-3.0.0-windows-x64-full")

    # 2) lite + addon（模拟真实用户：把 lite zip 解出的应用目录作为根，
    #    再将 addon 7z 解压进同一应用目录，使 _internal/dtug_ide/resources/mingw64 对齐）
    lite_dir = VERIFY / "lite_addon"
    lite_dir.mkdir(parents=True, exist_ok=True)
    print(">> 解压 lite 包 ...", flush=True)
    _extract_zip(LITE_ZIP, lite_dir)
    app_root = lite_dir / "DTUG-IDE-3.0.0-windows-x64"
    print(">> 解压 addon 包到应用目录 ...", flush=True)
    _extract_7z(ADDON_7Z, app_root)
    addon_ok = _check("lite+addon", app_root)

    print("\n=== 结果 ===")
    print(f"  full 编译器可发现:       {'通过' if full_ok else '失败'}")
    print(f"  lite+addon 可发现:       {'通过' if addon_ok else '失败'}")
    return 0 if (full_ok and addon_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
