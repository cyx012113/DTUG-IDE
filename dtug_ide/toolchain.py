"""C++ 工具链管理：发现系统中的 g++ / clang++，并自动探测其支持的 C++ 标准。

设计目标：
  * 优先使用内置（打包随附）的 MinGW64，其次自动识别本机已安装的 MSYS2 等；
  * 用户也可在设置里手动指定任意 g++.exe 路径；
  * 对任意编译器，用 ``-std=c++{id}`` 编译一段空脚本探测其支持的最高标准，
    先扫已知标准列表，再用二分法向上探测（例如 c++29），生成可选版本列表。

所有探测均在子进程中完成，使用 CREATE_NO_WINDOW 避免弹黑窗。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional

# Windows 下以无窗口方式启动子进程，避免探测时弹出黑窗
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 已知常见的 C++ 标准代号（数字部分），按版本升序。
# GCC 实际接受的令牌为 c++98 / c++03 / c++11 / c++14 / c++17 / c++20 / c++23 /
# c++26 / c++29（以及 gnu++ 变体）。更高版本由二分法自动发现。
KNOWN_STD_IDS = [98, 3, 11, 14, 17, 20, 23, 26]

# 探测结果缓存：{gpp 路径: [标准列表]}，避免重复调用 g++
_STD_CACHE: Dict[str, List[str]] = {}

# 中转（ASCII 化）工具链相关
_stage_lock = threading.Lock()
_STAGE_MARKER = ".dtug_stage_marker"


def _find_mingw_gpp(root: Path, max_levels: int = 5) -> str:
    """从 root 向上最多 max_levels 层，查找 mingw64/bin/g++.exe。

    用于兼容「编译器插件包解压到应用根目录 / _internal 目录」等不同落点。
    """
    cur = Path(root).resolve()
    for _ in range(max_levels):
        cand = cur / "mingw64" / "bin" / "g++.exe"
        if cand.exists():
            return str(cand)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return ""


def bundled_gpp() -> str:
    """返回打包内置的 g++.exe 路径（若存在），否则返回空串。

    搜索顺序：
      1. 冻结运行目录（onedir 的 _MEIPASS 或解压目录）下的
         dtug_ide/resources/mingw64/bin/g++.exe 与 mingw64/bin/g++.exe；
      2. 源码 / 开发目录下的 dtug_ide/resources/mingw64/bin/g++.exe；
      3. 从模块目录向上回退若干层查找 mingw64/bin/g++.exe
         （兼容插件包解压到应用根目录或 _internal 目录的情形）。
    """
    candidates: List[Path] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        # 打包时 resources 映射到 dtug_ide/resources（见 DTUG-IDE.spec 的 datas）
        candidates.append(Path(meipass) / "dtug_ide" / "resources" / "mingw64" / "bin" / "g++.exe")
        # 兼容：若有人把 mingw64 直接放在打包根目录
        candidates.append(Path(meipass) / "mingw64" / "bin" / "g++.exe")
    here = Path(__file__).resolve().parent  # .../dtug_ide
    candidates.append(here / "resources" / "mingw64" / "bin" / "g++.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    # 向上回退搜索（兼容 addon 解压在应用根目录或 _internal 目录的情形）
    found = _find_mingw_gpp(here)
    if found:
        return found
    if meipass:
        found = _find_mingw_gpp(Path(meipass))
        if found:
            return found
    return ""


def _mingw_roots() -> List[str]:
    """返回本机常见的 MinGW / MSYS2 安装根目录（含 bin 子目录）。"""
    roots: List[str] = []
    extra = [
        r"D:\MSYS2\mingw64",
        r"C:\msys64\mingw64",
        r"C:\MSYS2\mingw64",
        r"C:\mingw64",
        # 轻量 ucrt64 工具链（UCRT 运行时，体积远小于完整 mingw64）
        r"D:\MSYS2\ucrt64",
        r"C:\msys64\ucrt64",
        r"C:\MSYS2\ucrt64",
        r"C:\ucrt64",
        r"C:\Program Files\mingw-w64\bin",
        r"C:\Program Files (x86)\mingw-w64\bin",
    ]
    for base in extra:
        p = Path(base)
        if p.exists():
            roots.append(str(p))
    return roots


def discover_compilers() -> List[str]:
    """发现系统中可用的 C++ 编译器，按优先级排序后返回。

    优先级：内置 mingw64 > 本机 MSYS2/MinGW 路径 > PATH 中的 g++/clang++。
    若什么都没找到，返回 ["g++"] 以保证 UI 不为空。
    """
    found: List[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if not path:
            return
        key = path.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    # 1. 内置
    bundled = bundled_gpp()
    if bundled:
        add(bundled)

    # 2. 本机安装路径
    for root in _mingw_roots():
        bin_dir = Path(root) / "bin" if (Path(root).name != "bin") else Path(root)
        for exe in ("g++.exe", "clang++.exe"):
            p = bin_dir / exe
            if p.exists():
                add(str(p))

    # 3. PATH 中的命令
    for cmd in ("g++", "g++-14", "g++-13", "g++-12", "g++-11", "clang++"):
        loc = shutil.which(cmd)
        if loc:
            add(loc)

    if not found:
        return ["g++"]
    return found


def gpp_version(gpp: str) -> str:
    """返回编译器版本首行，失败返回 '?'。"""
    try:
        proc = subprocess.run(
            [gpp, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        return out.splitlines()[0] if out else "?"
    except Exception:
        return "?"


def _probe_cpp_standard(gpp: str, std_id: int) -> bool:
    """探测 ``g++ -std=c++{std_id}`` 是否能编译（用一段空脚本）。"""
    devnull = os.devnull
    cmd = [gpp, f"-std=c++{std_id}", "-x", "c++", "-", "-c", "-o", devnull]
    try:
        proc = subprocess.run(
            cmd,
            input=b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _binary_search_max_std(gpp: str, lo: int, hi: int) -> int:
    """二分查找 [lo, hi] 范围内 g++ 能接受的最大 -std=c++{id}。

    假定「支持性」随 id 单调不减（支持某标准则更低标准也支持），返回最高接受值。
    """
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if _probe_cpp_standard(gpp, mid):
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def detect_cpp_standards(gpp: str) -> List[str]:
    """检测给定编译器支持的 C++ 标准列表（如 ['c++98','c++11',...,'c++26']）。

    先扫描已知标准，若最高已知标准（c++26）受支持，再用二分法向上探测（c++29 等）。
    结果按版本号升序，并带缓存。
    """
    if not gpp:
        return []
    if gpp in _STD_CACHE:
        return list(_STD_CACHE[gpp])

    result: List[tuple[str, int]] = []
    seen: set[int] = set()
    for n in KNOWN_STD_IDS:
        if _probe_cpp_standard(gpp, n):
            result.append((f"c++{n}", n))
            seen.add(n)

    max_known = max((n for _, n in result), default=0)
    if max_known >= 26:
        # 向上二分探测更高标准（c++29、c++32 …）
        top = _binary_search_max_std(gpp, 27, 200)
        for n in range(27, top + 1):
            if n not in seen and _probe_cpp_standard(gpp, n):
                result.append((f"c++{n}", n))
                seen.add(n)

    result.sort(key=lambda x: x[1])
    standards = [s for s, _ in result]
    _STD_CACHE[gpp] = standards
    return standards


def _copy_resolving_symlinks(src: str, dst: str) -> None:
    """递归复制，遇到符号链接时用真实文件内容覆盖目标。

    解决 MSYS2 中 lib/libgcc.a、lib/gcc/.../default-manifest.o 等多级软链
    在复制后变成指向源机的断链、导致链接失败（cannot find crt2.o 等）的问题。
    """
    if os.path.islink(src):
        real = os.path.realpath(src)
        if os.path.isdir(real):
            os.makedirs(dst, exist_ok=True)
            _copy_resolving_symlinks(real, dst)
        else:
            parent = os.path.dirname(dst)
            os.makedirs(parent, exist_ok=True)
            if os.path.lexists(dst):
                try:
                    os.remove(dst)
                except OSError:
                    pass
            shutil.copy2(real, dst)
        return
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            _copy_resolving_symlinks(os.path.join(src, name), os.path.join(dst, name))
    else:
        parent = os.path.dirname(dst)
        os.makedirs(parent, exist_ok=True)
        shutil.copy2(src, dst)


def copy_toolchain(src_root: str, dst_root: str) -> None:
    """把一份 MinGW64 工具链从 src_root 复制到 dst_root（用于「捆绑内置工具链」）。

    src_root 通常是某个 mingw64 目录（含 bin/ lib/ include/ ...）。复制后
    dst_root/bin/g++.exe 应可被 bundled_gpp() 发现。

    复制时解析符号链接为真实文件内容（见 _copy_resolving_symlinks），避免
    源工具链中的软链在目标处变成断链。
    """
    src = Path(src_root)
    dst = Path(dst_root)
    # 先清空目标，保证是一份干净的工具链（用户主动触发的「捆绑」操作）
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except Exception as e:
            sys.stderr.write(f"[toolchain] 清空目标失败 {dst}: {e}\n")
    try:
        _copy_resolving_symlinks(str(src), str(dst))
    except Exception as e:
        sys.stderr.write(f"[toolchain] 复制失败: {e}\n")


def _is_ascii_path(p: str) -> bool:
    try:
        return bool(p) and p.isascii()
    except Exception:
        return False


def _ascii_stage_dir(src_mingw_root: str) -> str:
    """选择纯 ASCII 的中转目录，必须以 mingw64 结尾以保持可重定位标记。

    mingw-w64 的 g++ 会依据 ``<root>/mingw64/bin/g++.exe`` 这种目录名
    （mingw64 作为标记）自动推导系统 include/lib 路径，因此中转目录的
    末级必须是 mingw64。
    """
    drive = os.path.splitdrive(src_mingw_root)[0] or "C:"
    bases: List[str] = []
    try:
        td = tempfile.gettempdir()
        if td and _is_ascii_path(td):
            bases.append(td)
    except Exception:
        pass
    bases.append(f"{drive}\\")
    bases.append("C:\\")
    for b in bases:
        if b and _is_ascii_path(b):
            return os.path.join(b, "dtug_ide_toolchain", "mingw64")
    return os.path.join("C:\\", "dtug_ide_toolchain", "mingw64")


def resolve_ascii_gpp(gpp: str) -> str:
    """返回可用于实际编译（含链接）的 g++.exe 路径。

    mingw-w64 的 ``ld.exe`` 无法打开路径中含非 ASCII 字符（如中文）的文件，
    因此当 g++ 位于中文目录时，链接阶段会报 ``cannot find crt2.o / libstdc++``
    等错误。此函数把整份工具链复制（并解析符号链接为真实文件）到纯 ASCII 的
    中转目录，并缓存复用（按源 mingw64 目录的 mtime 判定是否过期），避免每次
    都复制 1GB+ 文件。若 gpp 路径本身已是 ASCII，则原样返回（零开销）。

    注意：仅「实际编译链接」需要 ASCII 路径；版本探测用 ``-c`` 仅编译不链接，
    在中文路径下也能正常工作，无需中转。
    """
    if not gpp or _is_ascii_path(gpp):
        return gpp
    src_mingw = os.path.dirname(os.path.dirname(gpp))  # .../mingw64
    if not os.path.isdir(src_mingw):
        return gpp
    stage = _ascii_stage_dir(src_mingw)
    staged_gpp = os.path.join(stage, "bin", "g++.exe")
    marker = os.path.join(stage, _STAGE_MARKER)
    need_copy = True
    if os.path.exists(staged_gpp) and os.path.exists(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                stored = f.read().strip()
            src_mtime = os.path.getmtime(src_mingw)
            if stored == f"{src_mingw}|{src_mtime}":
                need_copy = False
        except Exception:
            need_copy = True
    if need_copy:
        with _stage_lock:
            # 进入锁后再次确认，避免并发重复复制
            if not (os.path.exists(staged_gpp) and os.path.exists(marker)):
                try:
                    if os.path.exists(stage):
                        shutil.rmtree(stage)
                except Exception:
                    pass
                _copy_resolving_symlinks(src_mingw, stage)
                try:
                    os.makedirs(stage, exist_ok=True)
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write(f"{src_mingw}|{os.path.getmtime(src_mingw)}")
                except Exception:
                    pass
    return staged_gpp
