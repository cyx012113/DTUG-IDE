"""OI-style judging with sandbox isolation per test case."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .compiler import CompileResult, Compiler, RunResult
from .utils import ensure_dir, safe_remove


@dataclass
class TestCase:
    name: str
    input: str = ""
    expected: str = ""


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
    overall_status: str = ""  # ok, compile_error


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

        for tc in tests:
            test_dir = Path(tempfile.mkdtemp(prefix="dtug_test_"))
            try:
                run_res = self.compiler.run_sandboxed(
                    exe_path or "",
                    input_data=tc.input,
                    time_limit=time_limit,
                    memory_limit_mb=memory_limit_mb,
                    working_dir=test_dir,
                )

                if run_res.status == "TLE":
                    results.append(TestResult(
                        name=tc.name, status="TLE", time=run_res.elapsed,
                        memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                        stderr=run_res.stderr, expected=tc.expected,
                    ))
                elif run_res.status == "MLE":
                    results.append(TestResult(
                        name=tc.name, status="MLE", time=run_res.elapsed,
                        memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                        stderr=run_res.stderr, expected=tc.expected,
                    ))
                elif run_res.status == "RE" or run_res.returncode != 0:
                    results.append(TestResult(
                        name=tc.name, status="RE", time=run_res.elapsed,
                        memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                        stderr=run_res.stderr, expected=tc.expected,
                    ))
                else:
                    # Compare stdout only (not stderr)
                    actual_lines = run_res.stdout.strip().splitlines()
                    expected_lines = tc.expected.strip().splitlines()
                    if actual_lines == expected_lines:
                        results.append(TestResult(
                            name=tc.name, status="AC", time=run_res.elapsed,
                            memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                            stderr=run_res.stderr, expected=tc.expected,
                        ))
                    else:
                        results.append(TestResult(
                            name=tc.name, status="WA", time=run_res.elapsed,
                            memory_mb=run_res.memory_mb, stdout=run_res.stdout,
                            stderr=run_res.stderr, expected=tc.expected,
                        ))
            except Exception as e:
                results.append(TestResult(
                    name=tc.name, status="RE", time=0.0, memory_mb=0.0,
                    stdout="", stderr=str(e), expected=tc.expected,
                ))
            finally:
                safe_remove(test_dir)

        # Clean up compiled exe
        self.compiler.cleanup(exe_path)

        return JudgeResult(
            compile_result=compile_res,
            results=results,
            overall_status="ok",
        )
