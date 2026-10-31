"""Drive the unmodified production CLI over a PTY and archive raw output."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pexpect

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
STAMP = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
TENANT = 'd3-smoke-' + STAMP
CASES = [
    ('name', '我叫 TS', '我叫什么？'),
    ('secret', '我的 API key 是 sk-test-human-acceptance-123', '你记得我的 API key 吗？'),
    ('language', '我最喜欢的编程语言是 Python', '我喜欢什么编程语言？'),
    ('uv', '以后 Python 项目优先用 uv', '我平时 Python 项目偏好用什么？'),
]
manifest = {
    'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'tenant_id': TENANT, 'python': sys.executable,
    'method': 'Unmodified main.py in fresh PTY process per turn; distinct session IDs, same tenant/user per pair. No DB reads during behavioral run. Default provider configuration.',
    'turns': [],
}
for case, statement, question in CASES:
    for phase, prompt in [('learn', statement), ('recall', question)]:
        session = f'{case}-{phase}'
        args = ['-B', '-u', 'main.py', '--db', str(OUT / 'runtime.sqlite'), '--tenant-id', TENANT, '--user-id', case, '--session-id', session]
        record = {'case': case, 'phase': phase, 'session_id': session, 'user_id': case, 'prompt': prompt, 'argv': [sys.executable, *args], 'started_at': datetime.datetime.now().isoformat(), 'raw_log': f'{case}_{phase}.raw.txt'}
        print(f'START {case}/{phase}: {prompt}', flush=True)
        started = time.monotonic()
        with (OUT / record['raw_log']).open('w', encoding='utf-8') as raw:
            child = pexpect.spawn(sys.executable, args, cwd=str(ROOT), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), encoding='utf-8', timeout=240)
            child.logfile_read = raw
            try:
                child.expect_exact('你: ')
                child.sendline(prompt)
                outcome = child.expect(['🤖 状态: [a-zA-Z_]+', '❌ Run 监视失败:[^\r\n]*', pexpect.EOF, pexpect.TIMEOUT])
                record['terminal_observation'] = str(child.after) if outcome < 2 else ['EOF', 'TIMEOUT'][outcome - 2]
                if outcome < 2:
                    while True:
                        try:
                            child.read_nonblocking(size=65536, timeout=2)
                        except pexpect.TIMEOUT:
                            break
                        except pexpect.EOF:
                            break
                    child.sendline('exit')
                    child.expect(pexpect.EOF, timeout=30)
                elif outcome == 3:
                    child.sendline('/cancel')
                    record['harness_timeout_seconds'] = 240
                    try:
                        child.expect('🤖 状态:', timeout=30)
                        time.sleep(1)
                        child.sendline('exit')
                        child.expect(pexpect.EOF, timeout=15)
                    except (pexpect.TIMEOUT, pexpect.EOF):
                        pass
            except (pexpect.TIMEOUT, pexpect.EOF) as exc:
                record['harness_error'] = type(exc).__name__
            finally:
                child.close(force=True)
                record['exit_code'] = child.exitstatus
                record['signal'] = child.signalstatus
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        manifest['turns'].append(record)
        (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"END {case}/{phase}: {record.get('terminal_observation', record.get('harness_error'))}, {record['elapsed_seconds']}s", flush=True)
print('COMPLETE', flush=True)
