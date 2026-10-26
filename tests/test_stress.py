"""对拍器（StressWorker）真实编译测试。

用本机 g++ 真编译三个小程序，验证：
  * 输出不一致（WA）能被抓到并保存现场；
  * 待测程序运行时错误（RE）能被抓到；
  * 正确程序跑满轮数不误报；
  * 行级最小化真的把反例变小；
  * 生成器 / 标程自身异常时有明确中止信息。

依赖本机可发现的 g++（与 tests/test_compiler.py 同一前提）。
"""

import tempfile
from pathlib import Path

import pytest

from dtug_ide.compiler import Compiler
from dtug_ide.stress import StressWorker, _normalize

GEN = """\
#include <iostream>
using namespace std;
int main() { cout << "3\\n1 2 3\\n"; return 0; }
"""

BRUTE = """\
#include <iostream>
using namespace std;
int main() {
    int n; if (!(cin >> n)) return 0;
    long long s = 0, x;
    for (int i = 0; i < n; i++) { cin >> x; s += x; }
    cout << s << '\\n'; return 0;
}
"""

MAIN_OK = BRUTE  # 与标程一致 ⇒ 永远不发现反例

MAIN_WA = """\
#include <iostream>
using namespace std;
int main() {
    int n; if (!(cin >> n)) return 0;
    long long s = 1, x;   // bug：初始写成 1
    for (int i = 0; i < n; i++) { cin >> x; s += x; }
    cout << s << '\\n'; return 0;
}
"""

MAIN_RE = """\
#include <iostream>
int main() { int* p = nullptr; *p = 1; return 0; }
"""


@pytest.fixture(scope="module")
def compiler():
    return Compiler(Path(tempfile.gettempdir()) / "dtug_pytest_stress")


def _run(compiler: Compiler, gen: str, brute: str, main: str, **kw) -> dict:
    """同步跑一个对拍任务，收集 found / finished 结果。"""
    out = {"found": [], "progress": [], "finished": []}
    worker = StressWorker(compiler, gen, brute, main, rounds=kw.pop("rounds", 5), **kw)
    worker.found.connect(lambda info: out["found"].append(info))
    worker.progress.connect(lambda text: out["progress"].append(text))
    worker.finished.connect(lambda text: out["finished"].append(text))
    worker.run()  # 直接调用（同线程直连，无需事件循环）
    return out


def test_normalize():
    assert _normalize("a\n\n") == "a"
    assert _normalize("a\nb") == "a\nb"
    assert _normalize("  ") == ""


def test_decoder_folds_crlf():
    """回归：MinGW 文本模式会把 \\n 写成 \\r\\n，_decode_bytes 必须折叠，
    否则保存的对拍输入 / 数据生成器的 .in 会混入 \\r 污染数据。"""
    from dtug_ide.compiler import _decode_bytes

    assert _decode_bytes(b"3\r\n1 2 3\r\n") == "3\n1 2 3\n"
    assert _decode_bytes(b"a\rb") == "a\rb"  # 单独的 \r 保留（进度条场景）
    assert _decode_bytes("中文\n".encode("utf-8")) == "中文\n"


def test_real_program_stdout_has_no_cr(compiler):
    """真编译一个输出多行的程序，验证沙箱捕获的 stdout 不含 \\r。"""
    src = '#include <iostream>\nint main(){ std::cout << "a\\nb\\n"; return 0; }\n'
    res = compiler.compile(src)
    assert res.success
    run = compiler.run_sandboxed(
        res.exe_path, input_data="", time_limit=5.0, memory_limit_mb=512.0
    )
    assert run.success and run.stdout == "a\nb\n"
    compiler.cleanup(res.exe_path)


def test_stress_finds_wa(compiler, tmp_path):
    # 关最小化：校验保存的是「原始」反例现场
    out = _run(
        compiler, GEN, BRUTE, MAIN_WA, rounds=5, shrink=False,
        save_dir=str(tmp_path / "wa"),
    )
    assert out["found"], "有 bug 的主程序必须被发现"
    assert out["finished"] and "反例" in out["finished"][-1]
    info = out["found"][0]
    assert info["reason"] == "输出不一致（WA）"
    assert info["shrunk"] is False
    case_dir = Path(info["dir"])
    assert (case_dir / "input.txt").is_file()
    assert (case_dir / "brute.out").is_file()
    assert (case_dir / "main.out").is_file()
    assert (case_dir / "reason.txt").is_file()
    # 原始输入 3\n1 2 3\n：brute=6，main（初值 bug 写成 1）=7
    assert info["input"] == "3\n1 2 3\n"
    assert info["brute"].strip() == "6" and info["main"].strip() == "7"


def test_stress_finds_re(compiler, tmp_path):
    out = _run(compiler, GEN, BRUTE, MAIN_RE, rounds=3, save_dir=str(tmp_path / "re"))
    assert out["found"], "运行时错误必须被发现"
    assert "运行异常" in out["found"][0]["reason"]
    assert "RE" in out["found"][0]["reason"]


def test_stress_all_pass(compiler, tmp_path):
    out = _run(compiler, GEN, BRUTE, MAIN_OK, rounds=8, save_dir=str(tmp_path / "ok"))
    assert not out["found"], "正确程序不应误报"
    assert out["finished"] and "全部通过" in out["finished"][-1]


def test_stress_shrink(compiler, tmp_path):
    """最小化：反例输入应从多行收缩到更少行，且删后仍是反例。"""
    out = _run(
        compiler, GEN, BRUTE, MAIN_WA, rounds=1, shrink=True,
        save_dir=str(tmp_path / "shrink"),
    )
    assert out["found"]
    info = out["found"][0]
    assert info["shrunk"] is True
    shrunk_lines = [l for l in info["input"].split("\n") if l.strip()]
    assert len(shrunk_lines) < 2, f"最小化后应少于 2 行，实际：{shrunk_lines}"
    # 最小化后的输入仍必须让两边不一致（否则是最化错了）
    assert info["brute"].strip() != info["main"].strip()


def test_stress_broken_generator_aborts(compiler, tmp_path):
    bad_gen = "int main() { return 1; }  // 不输出任何数据"
    out = _run(compiler, bad_gen, BRUTE, MAIN_OK, rounds=3, save_dir=str(tmp_path / "badgen"))
    assert not out["found"]
    assert out["finished"] and "生成器" in out["finished"][-1]


def test_stress_compile_error_reports(compiler, tmp_path):
    out = _run(compiler, GEN, "this is not c++", MAIN_OK, rounds=3, save_dir=str(tmp_path / "ce"))
    assert not out["found"]
    assert out["finished"] and "编译失败" in out["finished"][-1]
