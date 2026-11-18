from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from zipfile import ZipFile

import build_windows_release as release


class WindowsReleaseSmokeTests(unittest.TestCase):
    def test_extracted_release_starts_and_configures_opencode_offline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'release.zip'
            release.build_release(release.ROOT, archive)
            extracted = root / 'Jev test folder'
            with ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                bundle.extractall(extracted)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, JEV_ROUTER_PORT=str(port), JEV_DISABLE_TIERS='opus,opus',
                       POLZA_API_KEY='offline-test-not-a-real-key', POLZA_BASE='http://127.0.0.1:1',
                       XDG_CONFIG_HOME=str(root / 'config'))
            if os.name == 'nt':
                command = ['cmd.exe', '/d', '/c', 'start_jev_no_anthropic.bat']
                setup = ['cmd.exe', '/d', '/c', 'setup_opencode_jev.bat']
                cwd = extracted
            else:
                command = [sys.executable, str(extracted / 'jev_router.py'), '--disable', 'haiku,sonnet,opus']
                setup = [sys.executable, str(extracted / 'setup_opencode_jev.py')]
                cwd = root
            with (root / 'router.log').open('w+b') as log, subprocess.Popen(
                command, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            ) as process:
                try:
                    deadline = time.monotonic() + 8
                    while True:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            self.fail(f'Jev did not start: {log.read().decode(errors="replace")}')
                        conn = HTTPConnection('127.0.0.1', port, timeout=1)
                        try:
                            conn.request('GET', '/v1/models')
                            response = conn.getresponse()
                            self.assertEqual(response.status, 200)
                            models = {model['id'] for model in json.loads(response.read())['data']}
                        except ConnectionRefusedError:
                            time.sleep(0.05)
                            continue
                        finally:
                            conn.close()
                        break
                    self.assertIn('auto', models)
                    self.assertGreater(len(models), 1)
                    self.assertFalse(any(model.startswith('anthropic/') for model in models))
                    for headers, body, expected in (
                        ({'Origin': 'https://example.test'}, '{}', 403),
                        ({}, '[]', 400),
                    ):
                        conn = HTTPConnection('127.0.0.1', port, timeout=1)
                        try:
                            conn.request('POST', '/v1/chat/completions', body, headers)
                            response = conn.getresponse()
                            self.assertEqual(response.status, expected)
                            response.read()
                        finally:
                            conn.close()

                    result = subprocess.run(setup, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                            capture_output=True, timeout=8)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    config = json.loads((root / 'config/opencode/opencode.json').read_bytes())
                    self.assertEqual(config['providers']['jev']['settings']['baseURL'],
                                     f'http://127.0.0.1:{port}/v1')
                finally:
                    if os.name == 'nt' and process.poll() is None:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       capture_output=True, timeout=8)
                    process.kill()
                    process.wait(timeout=8)

    def test_startup_rejects_missing_key_and_invalid_port(self):
        for key, port, expected in (('', '8787', 'POLZA_API_KEY'),
                                    ('offline-test-not-a-real-key', '0', 'JEV_ROUTER_PORT')):
            with self.subTest(expected=expected):
                result = subprocess.run([sys.executable, str(release.ROOT / 'jev_router.py')],
                                        env=dict(os.environ, POLZA_API_KEY=key, JEV_ROUTER_PORT=port,
                                                 JEV_DISABLE_TIERS=''), capture_output=True, text=True, timeout=8)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn('listening', result.stdout)


if __name__ == '__main__':
    unittest.main()
