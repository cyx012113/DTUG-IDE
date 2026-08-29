"""OI-style judging with sandbox isolation per test case."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .compiler import CompileResult, Compiler, RunResult
from .utils import ensure_dir, safe_remove


def _normalize_output(text: str, ignore_case: bool) -> List[str]:
    """把输出规范化为「可比较」的行列表（仅用于 AC/WA 判定，不改变展示内容）：

    - 每行去掉行末空格；
    - 去掉末尾的空行；
    - 若 ``ignore_case`` 为真，整体转为小写后再比较。

    注意：不会去除行首空格，也不会改动中间空行，以尽量贴合题目输出要求。
    """
    lines = text.splitlines()
    lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    if ignore_case:
        lines = [ln.lower() for ln in lines]
    return lines


@dataclass
class TestCase:
    name: str
    input: str = ""
    expected: str = ""


@dataclass
class JudgeControl:
    """评测运行控制：由 UI 线程设置标志，评测线程（run_sandboxed 循环）读取。

    - stop:  置 True 后，当前用例跑完即停止后续用例（并立即终止正在运行的子进程）。
    - pause: 置 True 后，每个用例之间挂起，直到置回 False（resume）。
    """

    stop: bool = False
    pause: bool = False


@dataclass
class TestResult:
    name: str
    status: str  # AC, WA, TLE, MLE, RE, CE
    time: float = 0.0
    memory_mb: float = 0.0
    stdout: str = ""
    stderr: str = ""
    expected: str = ""


@dataclass
class JudgeResult:
    compile_result: CompileResult
    results: List[TestResult] = field(default_factory=list)
    overall_status: str = ""  # ok, compile_error, stopped
    stopped: bool = False  # 是否因用户「停止」而提前结束


class JudgeEngine:
    """Sandboxed OI-style judge engine.

    Fixes from bug_report.md:
    - Each test case runs in its own temporary directory (isolation).
    - stdout and stderr are kept separate; only stdout is compared.
    - Pipe deadlock avoided via threaded reads in Compiler.run_sandboxed.
    - Time/memory limits are validated.
    - Process tree is killed on timeout.
    - No stderr mixing into WA comparison.
    """

    def __init__(self, compiler: Compiler) -> None:
        self.compiler = compiler

    def judge(
        self,
        source_code: str,
        tests: List[TestCase],
        time_limit: float = 1.0,
        memory_limit_mb: float = 256.0,
        on_result: Optional[Callable[[TestResult, int], None]] = None,
        control: Optional[JudgeControl] = None,
        ignore_case: bool = False,
    ) -> JudgeResult:
        time_limit = max(0.1, min(float(time_limit), 60.0))
        memory_limit_mb = max(1.0, min(float(memory_limit_mb), 4096.0))

        compile_res = self.compiler.compile(source_code)
        if not compile_res.success:
            return JudgeResult(compile_result=compile_res, overall_status="compile_error")

        exe_path = compile_res.exe_path
        results: List[TestResult] = []

        # Warm-up run (not counted)
        if tests:
            warmup_dir = Path(tempfile.mkdtemp(prefix="dtug_warmup_"))
            try:
                self.compiler.run_sandboxed(
                    exe_path or "",
                    input_data=tests[0].input,
                    time_limit=time_limit * 2,
                    memory_limit_mb=memory_limit_mb,
                    working_dir=warmup_dir,
                )
            except Exception:
                pass
            finally:
                safe_remove(warmup_dir)

        for idx, tc in enumerate(tests):
            # ---- 暂停 / 终止控制（在每用例开始前检查） ----
            if control is not None:
                if control.stop:
                    break
                # 暂停：挂起直到 resume（或期间被 stop）
                while control.pause and not control.stop:
                    time.sleep(0.1)
                if control.stop:
                    break

            test_dir = Path(tempfile.mkdtemp(prefix="dtug_test_"))
            result: Optional[TestResult] = None
            try:
                run_res = self.compiler.run_sandboxed(
                    exe_path or "",
                    input_data=tc.input,
                    time_limit=time_limit,
                    memory_limit_mb=memory_limit_mb,
                    working_dir=test_dir,
                )

                if run_res.status == "TLE":
                    result = TestResult(
                        name=tc.name, status="TLE", time=run_res.elapsed,
                        memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                        stderr=run_res.stderr, expected=tc.expected,
                    )
                elif run_res.status == "MLE":
                    result = TestResult(
                        name=tc.name, status="MLE", time=run_res.elapsed,
                        memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                        stderr=run_res.stderr, expected=tc.expected,
                    )
                elif run_res.status == "RE" or run_res.returncode != 0:
                    result = TestResult(
                        name=tc.name, status="RE", time=run_res.elapsed,
                        memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                        stderr=run_res.stderr, expected=tc.expected,
                    )
                else:
                    # 规范化后比较（去行末空格 / 去末尾空行；可选忽略大小写）
                    actual_lines = _normalize_output(run_res.stdout, ignore_case)
                    expected_lines = _normalize_output(tc.expected, ignore_case)
                    result = TestResult(
                        name=tc.name,
                        status="AC" if actual_lines == expected_lines else "WA",
                        time=run_res.elapsed,
                        memory_mb=run_res.memory_mb,
                        stdout=run_res.stdout,
                        stderr=run_res.stderr,
                        expected=tc.expected,
                    )
            except Exception as e:
                result = TestResult(
                    name=tc.name, status="RE", time=0.0, memory_mb=0.0,
                    stdout="", stderr=str(e), expected=tc.expected,
                )
            finally:
                safe_remove(test_dir)

            results.append(result)
            # 每完成一个用例即回调（供 UI 增量刷新，实现「依次显示」）。
            if on_result is not None:
                on_result(result, idx)

        # Clean up compiled exe
        self.compiler.cleanup(exe_path)

        # 因「停止」而提前结束（仍有未跑的用例）
        stopped = bool(control and control.stop) and len(results) < len(tests)
        return JudgeResult(
            compile_result=compile_res,
            results=results,
            overall_status="stopped" if stopped else "ok",
            stopped=stopped,
        )
