"""Tests for DTUG IDE compiler and judge engine."""

import tempfile
from pathlib import Path

import pytest

from dtug_ide.compiler import Compiler
from dtug_ide.judge import JudgeEngine, TestCase


@pytest.fixture
def tmp_compiler():
    tmp = Path(tempfile.gettempdir()) / "dtug_pytest"
    tmp.mkdir(exist_ok=True)
    c = Compiler(tmp)
    yield c
    c.cleanup()


def test_compile_hello(tmp_compiler: Compiler) -> None:
    code = '#include <iostream>\nint main() { std::cout << "hello" << std::endl; return 0; }'
    res = tmp_compiler.compile(code)
    assert res.success is True
    assert res.exe_path is not None
    tmp_compiler.cleanup(res.exe_path)


def test_run_hello(tmp_compiler: Compiler) -> None:
    code = '#include <iostream>\nint main() { std::cout << "hello" << std::endl; return 0; }'
    res = tmp_compiler.compile(code)
    assert res.success is True
    run = tmp_compiler.run_sandboxed(res.exe_path, "", time_limit=5.0, memory_limit_mb=512.0)
    assert run.success is True
    assert run.stdout.strip() == "hello"
    tmp_compiler.cleanup(res.exe_path)


def test_chinese_output(tmp_compiler: Compiler) -> None:
    code = '#include <iostream>\nint main() { std::cout << "你好，世界" << std::endl; return 0; }'
    res = tmp_compiler.compile(code)
    assert res.success is True
    run = tmp_compiler.run_sandboxed(res.exe_path, "", time_limit=5.0, memory_limit_mb=512.0)
    assert "你好，世界" in run.stdout
    tmp_compiler.cleanup(res.exe_path)


def test_tle(tmp_compiler: Compiler) -> None:
    code = "#include <iostream>\nint main() { while(true); }"
    res = tmp_compiler.compile(code)
    assert res.success is True
    run = tmp_compiler.run_sandboxed(res.exe_path, "", time_limit=0.5, memory_limit_mb=256.0)
    assert run.status == "TLE"
    tmp_compiler.cleanup(res.exe_path)


def test_judge_ac(tmp_compiler: Compiler) -> None:
    code = '#include <iostream>\nint main() { int a,b; std::cin>>a>>b; std::cout<<a+b; return 0; }'
    je = JudgeEngine(tmp_compiler)
    tests = [TestCase("t1", "1 2\n", "3"), TestCase("t2", "3 5\n", "8")]
    jres = je.judge(code, tests, time_limit=1.0, memory_limit_mb=256.0)
    assert jres.overall_status == "AC"
    for r in jres.results:
        assert r.status == "AC"
