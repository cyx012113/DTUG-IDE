"""Compiler and runner logic with bug fixes."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .utils import ensure_dir, generate_uid, kill_process_tree, safe_remove


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


class Compiler:
    """Handle C++ compilation and execution."""

    def __init__(self, temp_dir: str | Path, compiler_cmd: str = "g++") -> None:
        self.temp_dir = Path(temp_dir)
        ensure_dir(self.temp_dir)
        self.compiler_cmd = compiler_cmd
        self.std = "c++17"

    def set_compiler(self, cmd: str) -> None:
        self.compiler_cmd = cmd

    def compile(self, source_code: str, extra_flags: Optional[List[str]] = None) -> CompileResult:
        """Compile source code and return result."""
        uid = generate_uid()
        cpp_file = self.temp_dir / f"{uid}.cpp"
        exe_file = self.temp_dir / f"{uid}.exe" if os.name == "nt" else self.temp_dir / f"{uid}.out"

        try:
            cpp_file.write_text(source_code, encoding="utf-8")
        except Exception as e:
            return CompileResult(success=False, output="", error=f"写入源文件失败: {e}")

        cmd = [self.compiler_cmd, str(cpp_file), "-o", str(exe_file), f"-std={self.std}", "-Wall", "-Wextra", "-O2", "-pipe"]
        if extra_flags:
            cmd.extend(extra_flags)

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = proc.communicate(timeout=30)
            output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
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
        """Run executable in a separate terminal window."""
        if os.name == "nt":
            # Use start to open new cmd window; use & instead of && so pause always runs
            exe_path_quoted = f'"{exe_path}"'
            cmd = f'start "C++ 程序" /D "{self.temp_dir}" cmd /c "{exe_path_quoted} & pause"'
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
            )
        except Exception as e:
            return RunResult(success=False, stdout="", stderr="", returncode=-1, elapsed=0.0, error=str(e))

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

        stdout = bytes(stdout_data).decode("utf-8", errors="replace")
        stderr = bytes(stderr_data).decode("utf-8", errors="replace")

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
