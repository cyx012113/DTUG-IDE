import os
import subprocess
import uuid
import json
import sys
import time
from flask import Flask, request, jsonify, render_template

# 新增依赖：psutil（用于监控资源）
try:
    import psutil
except ImportError:
    print("请先安装 psutil: pip install psutil")
    sys.exit(1)

app = Flask(__name__)

# 临时目录（每次启动时清空旧文件）
TEMP_DIR = os.path.join(os.path.dirname(__file__), 'temp_compile')
os.makedirs(TEMP_DIR, exist_ok=True)

# 清空旧临时文件（可选）
for f in os.listdir(TEMP_DIR):
    try:
        os.remove(os.path.join(TEMP_DIR, f))
    except:
        pass

# ============================================================
#  首页 - 主编辑器
# ============================================================
@app.route('/')
def index():
    return render_template('index.html')

# ============================================================
#  编译运行（独立CMD窗口）
# ============================================================
@app.route('/compile', methods=['POST'])
def compile_code():
    """
    接收 JSON: {"code": "源代码", "compiler": "编译器名称"}
    编译成功后，在新的 CMD 窗口中运行程序，返回编译信息。
    """
    data = request.get_json()
    if not data or 'code' not in data:
        return jsonify({'error': '缺少 code 字段'}), 400

    source_code = data['code']
    uid = uuid.uuid4().hex[:8]
    cpp_file = os.path.join(TEMP_DIR, f'{uid}.cpp')
    exe_file = os.path.join(TEMP_DIR, f'{uid}.exe' if os.name == 'nt' else f'{uid}.out')

    # 写入源文件（UTF-8 无 BOM）
    with open(cpp_file, 'w', encoding='utf-8') as f:
        f.write(source_code)

    # 编译命令（使用 -pipe 加速）
    compile_cmd = [
        'g++', cpp_file,
        '-o', exe_file,
        '-std=c++17',
        '-Wall', '-Wextra',
        '-O2',
        '-pipe'
    ]

    try:
        compile_proc = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            shell=True if os.name == 'nt' else False
        )
    except subprocess.TimeoutExpired:
        return jsonify({'status': 1, 'output': '编译超时 (30s)', 'program_output': ''})

    compile_output = compile_proc.stdout + compile_proc.stderr

    if compile_proc.returncode != 0:
        # 编译失败
        return jsonify({
            'status': 1,
            'output': compile_output,
            'program_output': ''
        })

    # 编译成功，打开独立 CMD 窗口运行程序，结束后暂停并等待按键关闭
    try:
        if os.name == 'nt':
            # 使用 && pause 实现程序结束暂停，按任意键关闭窗口
            cmd = f'start "C++ 程序" /D "{TEMP_DIR}" cmd /c "{exe_file} && pause"'
            subprocess.Popen(cmd, shell=True)
        else:
            # Linux/macOS 下简单后台运行（不会暂停）
            subprocess.Popen([exe_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({
            'status': 0,
            'output': compile_output,
            'program_output': '✅ 程序已在独立窗口中运行，运行结束后会暂停，按任意键关闭窗口。'
        })
    except Exception as e:
        return jsonify({
            'status': 1,
            'output': f'启动程序失败: {str(e)}',
            'program_output': ''
        })
    finally:
        # 清理源文件，保留 exe 便于重复运行
        try:
            os.remove(cpp_file)
        except:
            pass

# ============================================================
#  样例测试（用于多标签编辑器）
# ============================================================
@app.route('/test', methods=['POST'])
def run_tests():
    """
    接收 JSON: { "code": "源代码", "tests": [ { "name": "test1", "input": "1 2", "expected": "3" } ] }
    返回: { "results": [ { "name": "test1", "passed": true/false, "actual": "输出内容", "expected": "期望" } ] }
    """
    data = request.get_json()
    if not data or 'code' not in data or 'tests' not in data:
        return jsonify({'error': '缺少 code 或 tests 字段'}), 400

    source_code = data['code']
    tests = data['tests']

    uid = uuid.uuid4().hex[:8]
    cpp_file = os.path.join(TEMP_DIR, f'{uid}.cpp')
    exe_file = os.path.join(TEMP_DIR, f'{uid}.exe' if os.name == 'nt' else f'{uid}.out')

    # 写入源文件
    with open(cpp_file, 'w', encoding='utf-8') as f:
        f.write(source_code)

    # 编译
    compile_cmd = [
        'g++', cpp_file,
        '-o', exe_file,
        '-std=c++17',
        '-Wall', '-Wextra',
        '-O2', '-pipe'
    ]
    try:
        compile_proc = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            shell=True if os.name == 'nt' else False
        )
    except subprocess.TimeoutExpired:
        return jsonify({'error': '编译超时 (30s)'})

    if compile_proc.returncode != 0:
        # 编译失败，返回错误信息
        return jsonify({
            'error': compile_proc.stdout + compile_proc.stderr,
            'results': []
        })

    # 对每个测试用例运行程序
    results = []
    for test in tests:
        test_name = test.get('name', '未命名测试')
        input_data = test.get('input', '')
        expected = test.get('expected', '')

        try:
            run_proc = subprocess.run(
                [exe_file],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=10,
                shell=True if os.name == 'nt' else False
            )
            actual = run_proc.stdout + run_proc.stderr
            # 判断是否通过：如果期望为空，则只要运行成功就算通过（退出码0）
            if expected:
                passed = expected in actual  # 宽松匹配：期望字符串出现在实际输出中
            else:
                passed = (run_proc.returncode == 0)
            results.append({
                'name': test_name,
                'passed': passed,
                'actual': actual.strip(),
                'expected': expected
            })
        except subprocess.TimeoutExpired:
            results.append({
                'name': test_name,
                'passed': False,
                'actual': '运行超时 (10s)',
                'expected': expected
            })

    # 清理临时文件（可选）
    try:
        os.remove(cpp_file)
        os.remove(exe_file)
    except:
        pass

    return jsonify({'results': results})

# ============================================================
#  批量测评（OI 风格，带时间/空间限制）
# ============================================================
@app.route('/judge', methods=['GET'])
def judge_page():
    """渲染测评页面"""
    return render_template('judge.html')
@app.route('/judge', methods=['POST'])
def judge_code():
    data = request.get_json()
    if not data or 'code' not in data or 'tests' not in data:
        return jsonify({'error': '缺少参数'}), 400

    source_code = data['code']
    tests = data['tests']
    time_limit = float(data.get('time_limit', 1.0))
    memory_limit = float(data.get('memory_limit', 256))

    # ---------- 编译 ----------
    uid = uuid.uuid4().hex[:8]
    cpp_file = os.path.join(TEMP_DIR, f'{uid}.cpp')
    exe_file = os.path.join(TEMP_DIR, f'{uid}.exe' if os.name == 'nt' else f'{uid}.out')

    with open(cpp_file, 'w', encoding='utf-8') as f:
        f.write(source_code)

    compile_cmd = ['g++', cpp_file, '-o', exe_file, '-std=c++17', '-Wall', '-Wextra', '-O2', '-pipe']
    try:
        compile_proc = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            shell=True if os.name == 'nt' else False
        )
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'compile_error', 'compile_output': '编译超时 (30s)', 'results': []})

    if compile_proc.returncode != 0:
        return jsonify({
            'status': 'compile_error',
            'compile_output': compile_proc.stdout + compile_proc.stderr,
            'results': []
        })

    # ---------- 预热：用第一个测试点的输入运行一次（不计结果） ----------
    if tests:
        first_test = tests[0]
        try:
            warmup_proc = subprocess.Popen(
                [exe_file],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                shell=True if os.name == 'nt' else False
            )
            if first_test.get('input'):
                warmup_proc.stdin.write(first_test['input'])
            warmup_proc.stdin.close()
            # 等待程序结束或超时（给足够时间让程序运行完，但不要超过时间限制的两倍）
            try:
                warmup_proc.wait(timeout=time_limit * 2)
            except subprocess.TimeoutExpired:
                warmup_proc.kill()
                warmup_proc.wait()
        except Exception:
            pass  # 预热失败不影响评测

    # ---------- 正式运行测试 ----------
    results = []
    for test in tests:
        input_data = test.get('input', '')
        expected = test.get('expected', '')

        proc = None
        try:
            proc = subprocess.Popen(
                [exe_file],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True if os.name == 'nt' else False
            )

            if input_data:
                proc.stdin.write(input_data)
            proc.stdin.close()

            start_time = time.time()
            max_memory = 0
            terminated = False
            p = psutil.Process(proc.pid)

            while proc.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > time_limit:
                    p.terminate()
                    time.sleep(0.1)
                    if p.is_running():
                        p.kill()
                    proc.wait()
                    results.append({
                        'status': 'TLE',
                        'time': elapsed,
                        'memory': max_memory,
                        'output': ''
                    })
                    terminated = True
                    break

                try:
                    mem_info = p.memory_info()
                    mem_mb = mem_info.rss / (1024 * 1024)
                    if mem_mb > max_memory:
                        max_memory = mem_mb
                    if mem_mb > memory_limit:
                        p.terminate()
                        time.sleep(0.1)
                        if p.is_running():
                            p.kill()
                        proc.wait()
                        results.append({
                            'status': 'MLE',
                            'time': elapsed,
                            'memory': max_memory,
                            'output': ''
                        })
                        terminated = True
                        break
                except psutil.NoSuchProcess:
                    break

                time.sleep(0.005)

            if not terminated:
                stdout, stderr = proc.communicate(timeout=1)
                elapsed = time.time() - start_time
                try:
                    mem_info = p.memory_info()
                    mem_mb = mem_info.rss / (1024 * 1024)
                    if mem_mb > max_memory:
                        max_memory = mem_mb
                except:
                    pass

                output = stdout
                if proc.returncode != 0:
                    status = 'RE'
                    output = stdout + stderr
                else:
                    actual_lines = output.strip().splitlines()
                    expected_lines = expected.strip().splitlines()
                    if actual_lines == expected_lines:
                        status = 'AC'
                    else:
                        status = 'WA'

                results.append({
                    'status': status,
                    'time': elapsed,
                    'memory': max_memory,
                    'output': output
                })
        except Exception as e:
            if proc:
                try:
                    p.kill()
                except:
                    pass
            results.append({
                'status': 'RE',
                'time': 0,
                'memory': 0,
                'output': str(e)
            })

    # 清理临时文件
    try:
        os.remove(cpp_file)
        os.remove(exe_file)
    except:
        pass

    return jsonify({
        'status': 'ok',
        'compile_output': compile_proc.stdout + compile_proc.stderr,
        'results': results
    })
# ============================================================
#  启动服务
# ============================================================
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)