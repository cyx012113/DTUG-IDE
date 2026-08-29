"""清理验证产物（release/_verify 与各类日志），并列出最终发布物。"""
import os
import shutil
import stat
from pathlib import Path


def _onerr(func, p, exc):
    try:
        os.chmod(p, 0o777)
        func(p)
    except Exception as e:  # noqa: BLE001
        print("fail", p, e)


def main() -> None:
    targets = [
        "release/_verify",
        "release/mr.log",
        "release/verify.log",
        "release/diag.log",
        "release/diag2.log",
        "release/exelaunch.log",
    ]
    for t in targets:
        q = Path(t)
        if q.is_dir():
            shutil.rmtree(q, onexc=_onerr)
            print("rmdir", t)
        elif q.exists():
            try:
                os.remove(q)
                print("rm", t)
            except Exception as e:  # noqa: BLE001
                print("rmfail", t, e)
        else:
            print("skip", t)
    print("--- final release artifacts ---")
    for f in sorted(Path("release").glob("*")):
        if f.suffix in (".7z", ".zip") or f.name == "SHA256SUMS.txt":
            print(f"{f.name}: {f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
