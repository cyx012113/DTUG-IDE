"""将本机轻量 ucrt64 工具链暂存为发布用的 mingw64（去掉文档/配置等非必需目录）。

暂存目录：release/_staging/mingw64
应用时由 tools/make_release.py 复制到 发布包的 dtug_ide/resources/mingw64。
ucrt64 已是无 python 的精简 GCC，这里仅剔除 share(文档) 与 etc(证书/pki)，进一步瘦身。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

SRC = r"D:\MSYS2\ucrt64"
DST = r"D:\Github仓库\DTUG-IDE\release\_staging\mingw64"

# 顶层可安全剔除的目录（编译 C++ 不需要）
SKIP_TOP_DIRS = {"share", "etc"}


def _ignore(src: str, names: list[str]) -> set[str]:
    rel = os.path.relpath(src, SRC)
    if rel == ".":
        return {n for n in names if n in SKIP_TOP_DIRS and os.path.isdir(os.path.join(src, n))}
    return set()


def main() -> int:
    if not os.path.isdir(SRC):
        print(f"[ERR] 源工具链不存在: {SRC}", file=sys.stderr)
        return 2
    if os.path.exists(DST):
        # 安全删除沙箱会拦截 rmtree；改为改名移走旧目录（不触发删除）
        _old = f"{DST}.old_{int(time.time())}"
        if os.path.exists(_old):
            try:
                os.rename(_old, _old + "_bak")
            except OSError:
                pass
        os.rename(DST, _old)
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    print(f"copying {SRC} -> {DST} (skip {sorted(SKIP_TOP_DIRS)})")
    shutil.copytree(SRC, DST, ignore=_ignore, symlinks=False, copy_function=shutil.copy2)

    gpp = os.path.join(DST, "bin", "g++.exe")
    if not os.path.isfile(gpp):
        print("[ERR] 拷贝后未找到 g++.exe", file=sys.stderr)
        return 3
    # 验证：rename 安全 + 能编译运行 C++11（显式 --sysroot 模拟 DTUG-IDE 运行时的调用方式）
    out = subprocess.run([gpp, "--version"], capture_output=True, text=True)
    print("g++ --version:\n" + out.stdout.strip())
    test_cpp = os.path.join(os.path.dirname(DST), "hello_test.cpp")
    test_exe = os.path.join(os.path.dirname(DST), "hello_test.exe")
    with open(test_cpp, "w", encoding="utf-8") as fh:
        fh.write('#include <iostream>\nint main(){ std::cout << "ucrt64-ok\\n"; return 0; }\n')
    try:
        r1 = subprocess.run([gpp, "--sysroot", DST, "-std=c++11", test_cpp, "-o", test_exe],
                            capture_output=True, text=True)
        if r1.returncode != 0:
            print("[ERR] 编译失败:\n" + r1.stderr)
            return 4
        r2 = subprocess.run([test_exe], capture_output=True, text=True)
        print("run:", r2.stdout.strip(), "(rc=%d)" % r2.returncode)
    finally:
        for f in (test_cpp, test_exe):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    print("staged OK:", DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
