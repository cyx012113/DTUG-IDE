"""Compiler and runner logic with bug fixes."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .toolchain import resolve_ascii_gpp
from .utils import ensure_dir, generate_uid, kill_process_tree, safe_remove

# Windows 下以无窗口方式启动子进程，避免编译/运行 g++ 与程序时弹出黑窗
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 默认编译/连接指令（Dev-C++ 风格，可在设置中覆盖）
# 默认：静态链接 libgcc、C++14 标准、O2 优化（与用户要求一致）。
# 注意：-std 由编译器 std 字段单独控制，避免与下方重复；实际命令为
# ``-static-libgcc -O2 -std=c++14``，等价于用户要求的默认指令。
DEFAULT_STD = "c++14"
DEFAULT_COMPILE_FLAGS = "-static-libgcc -O2"
DEFAULT_LINK_FLAGS = ""

# 编译时始终追加的字符集相关选项：源文件统一按 UTF-8 读取，字符串字面量按
# UTF-8 输出，确保中文代码在任何环境下都能正确编译与显示（修复中文输出爆掉）。
CHARSET_FLAGS = ["-finput-charset=UTF-8", "-fexec-charset=UTF-8"]


def _decode_bytes(data: bytes) -> str:
    """把子进程输出字节安全解码为字符串。

    依次尝试 utf-8 / gbk / latin-1，最后回退到 utf-8(replace)，彻底避免
    中文或混合编码导致的解码异常（「输出中文可能会爆掉」的根因之一）。
    """
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@dataclass
class CompileResult:
    success: bool
    output: str
    exe_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RunResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int
    elapsed: float
    status: str = ""  # AC, WA, TLE, MLE, RE
    memory_mb: float = 0.0
    error: Optional[str] = None


def _split_flags(text: str) -> List[str]:
    """按 shell 规则拆分编译/连接指令（支持引号包裹的参数）。"""
    text = (text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


class Compiler:
    """Handle C++ compilation and execution."""

    def __init__(self, temp_dir: str | Path, compiler_cmd: str = "g++") -> None:
        self.temp_dir = Path(temp_dir)
        ensure_dir(self.temp_dir)
        self.compiler_cmd = compiler_cmd
        self.std = DEFAULT_STD
        self.compile_flags: List[str] = _split_flags(DEFAULT_COMPILE_FLAGS)
        self.link_flags: List[str] = _split_flags(DEFAULT_LINK_FLAGS)
        # 当前正在运行的沙箱子进程（用于「停止评测」时终止），无则为 None
        self._active_process: Optional[subprocess.Popen] = None

    def set_compiler(self, cmd: str) -> None:
        self.compiler_cmd = cmd

    def set_std(self, std: str) -> None:
        self.std = std or DEFAULT_STD

    def set_compile_flags(self, text: str) -> None:
        self.compile_flags = _split_flags(text)

    def set_link_flags(self, text: str) -> None:
        self.link_flags = _split_flags(text)

    def compile(self, source_code: str, extra_flags: Optional[List[str]] = None) -> CompileResult:
        """Compile source code and return result."""
        uid = generate_uid()
        cpp_file = self.temp_dir / f"{uid}.cpp"
        exe_file = self.temp_dir / f"{uid}.exe" if os.name == "nt" else self.temp_dir / f"{uid}.out"

        try:
            cpp_file.write_text(source_code, encoding="utf-8")
        except Exception as e:
            return CompileResult(success=False, output="", error=f"写入源文件失败: {e}")

        # 命令行顺序：源文件 → 输出 → 编译指令 → 默认编译选项 → 连接指令
        # （连接指令放在末尾，符合 g++/ld 的惯例；不影响单文件编译）
        # 若编译器路径含非 ASCII 字符（如中文目录），mingw-w64 的 ld.exe 无法
        # 打开其中的 crt*.o / libstdc++ 等，需经 resolve_ascii_gpp 中转为 ASCII 路径。
        effective_gpp = resolve_ascii_gpp(self.compiler_cmd)

        # ucrt64 等工具链的 sysroot 被硬编码为绝对路径 /ucrt64（仅在 MSYS2 虚拟
        # 挂载中才存在）；脱离 MSYS2 直接作为 Windows 进程启动时找不到头文件/库。
        # 显式用 --sysroot 指回工具链实际根目录，使其成为可重定位编译器。
        # 对本身可重定位的 mingw64 而言 --sysroot 指向自身根目录，是空操作，安全。
        sysroot_flags: List[str] = []
        _gpp_root = Path(effective_gpp).parent.parent
        if _gpp_root.name in ("mingw64", "ucrt64"):
            sysroot_flags = ["--sysroot", str(_gpp_root)]

        cmd = [effective_gpp, *sysroot_flags, str(cpp_file), "-o", str(exe_file)]
        cmd.extend(self.compile_flags)
        cmd.extend([f"-std={self.std}", "-pipe"])
        cmd.extend(self.link_flags)
        cmd.extend(CHARSET_FLAGS)
        if extra_flags:
            cmd.extend(extra_flags)

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            stdout, stderr = proc.communicate(timeout=30)
            output = _decode_bytes(stdout) + _decode_bytes(stderr)
            if proc.returncode != 0:
                safe_remove(cpp_file)
                safe_remove(exe_file)
                return CompileResult(success=False, output=output)
            safe_remove(cpp_file)
            return CompileResult(success=True, output=output, exe_path=str(exe_file))
        except subprocess.TimeoutExpired:
            if proc:
                kill_process_tree(proc.pid)
                proc.wait()
            safe_remove(cpp_file)
            safe_remove(exe_file)
            return CompileResult(success=False, output="编译超时 (30s)")
        except Exception as e:
            if proc:
                try:
                    kill_process_tree(proc.pid)
                except Exception:
                    pass
            safe_remove(cpp_file)
            safe_remove(exe_file)
            return CompileResult(success=False, output="", error=str(e))

    def run_in_terminal(self, exe_path: str) -> None:
        """Run executable in a separate terminal window (Dev-C++ 风格黑窗)。

        先切到 UTF-8 代码页（chcp 65001），保证程序输出的中文能正常显示，
        不会因控制台默认 GBK 代码页而乱码。
        """
        if os.name == "nt":
            # Use start to open new cmd window; use & instead of && so pause always runs
            exe_path_quoted = f'"{exe_path}"'
            cmd = (
                f'start "C++ 程序" /D "{self.temp_dir}" cmd /c '
                f'"chcp 65001 >nul & {exe_path_quoted} & pause"'
            )
            subprocess.Popen(cmd, shell=True)
        else:
            subprocess.Popen([exe_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run_sandboxed(
        self,
        exe_path: str,
        input_data: str = "",
        time_limit: float = 5.0,
        memory_limit_mb: float = 512.0,
        working_dir: Optional[str | Path] = None,
    ) -> RunResult:
        """Run executable with time/memory limits in an isolated working directory.

        Fixes from bug_report.md:
        - Uses threads to read stdout/stderr to avoid pipe deadlock.
        - Kills process tree on timeout.
        - Returns stdout and stderr separately.
        - Uses bytes decode with replace to avoid UnicodeDecodeError.
        - Validates time_limit and memory_limit.
        """
        time_limit = max(0.1, min(time_limit, 60.0))
        memory_limit_mb = max(1.0, min(memory_limit_mb, 4096.0))

        work_dir = Path(working_dir) if working_dir else self.temp_dir
        ensure_dir(work_dir)

        # Copy exe to working dir to avoid file lock issues when deleting later
        exe_name = Path(exe_path).name
        local_exe = work_dir / exe_name
        if Path(exe_path).resolve() != local_exe.resolve():
            try:
                shutil.copy2(exe_path, local_exe)
            except Exception as e:
                return RunResult(success=False, stdout="", stderr="", returncode=-1, elapsed=0.0, error=f"复制可执行文件失败: {e}")

        try:
            proc = subprocess.Popen(
                [str(local_exe)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(work_dir),
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            return RunResult(success=False, stdout="", stderr="", returncode=-1, elapsed=0.0, error=str(e))

        # 记录当前活动进程，供 kill_active() 在「停止评测」时终止
        self._active_process = proc

        stdout_data = bytearray()
        stderr_data = bytearray()

        def read_stdout():
            try:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    stdout_data.extend(chunk)
            except Exception:
                pass

        def read_stderr():
            try:
                while True:
                    chunk = proc.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_data.extend(chunk)
            except Exception:
                pass

        t_out = threading.Thread(target=read_stdout)
        t_err = threading.Thread(target=read_stderr)
        t_out.start()
        t_err.start()

        start = time.time()
        terminated = False
        status = ""
        max_memory = 0.0

        try:
            import psutil
            p = psutil.Process(proc.pid)
        except Exception:
            p = None

        try:
            if input_data:
                proc.stdin.write(input_data.encode("utf-8"))
            proc.stdin.close()
        except Exception:
            pass

        while proc.poll() is None:
            elapsed = time.time() - start
            if elapsed > time_limit:
                kill_process_tree(proc.pid)
                proc.wait()
                terminated = True
                status = "TLE"
                break

            if p is not None:
                try:
                    mem_mb = p.memory_info().rss / (1024 * 1024)
                    if mem_mb > max_memory:
                        max_memory = mem_mb
                    if mem_mb > memory_limit_mb:
                        kill_process_tree(proc.pid)
                        proc.wait()
                        terminated = True
                        status = "MLE"
                        break
                except psutil.NoSuchProcess:
                    break
                except Exception:
                    pass

            time.sleep(0.005)

        if not terminated:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid)
                proc.wait()
                terminated = True
                status = "TLE"

        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)

        elapsed = time.time() - start

        stdout = _decode_bytes(bytes(stdout_data))
        stderr = _decode_bytes(bytes(stderr_data))

        if terminated:
            return RunResult(
                success=False,
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode if proc.returncode is not None else -1,
                elapsed=elapsed,
                status=status,
                memory_mb=max_memory,
            )

        if proc.returncode != 0:
            return RunResult(
                success=False,
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
                elapsed=elapsed,
                status="RE",
                memory_mb=max_memory,
            )

        return RunResult(
            success=True,
            stdout=stdout,
            stderr=stderr,
            returncode=0,
            elapsed=elapsed,
            status="",
            memory_mb=max_memory,
        )

    def cleanup(self, exe_path: Optional[str]) -> None:
        """Remove executable and related files."""
        if exe_path:
            safe_remove(exe_path)

    def kill_active(self) -> None:
        """终止当前正在运行的沙箱子进程（用于「停止评测」）。

        已结束的进程会被忽略（poll() is None 才表示仍在运行），
        因此调用时即使没有活动进程也安全。
        """
        proc = getattr(self, "_active_process", None)
        if proc is not None and proc.poll() is None:
            try:
                kill_process_tree(proc.pid)
            except Exception:
                pass
