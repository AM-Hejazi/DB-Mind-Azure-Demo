"""Real serving-process regression for parallel Gradio asset connections."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from synthetic_demo.seed import seed_sqlite


@unittest.skipUnless(importlib.util.find_spec('uvicorn') and importlib.util.find_spec('gradio'),
                     'Serving dependencies are required')
class HTTPCapacityTests(unittest.TestCase):
    def test_parallel_connections_leave_ui_and_authentication_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root/'fixture.sqlite3'
            seed_sqlite(fixture)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            env = {k: v for k, v in os.environ.items() if not k.startswith(('DB_', 'APP_', 'DEEPSEEK_'))}
            env.update(APP_AUTH='test:offline-capacity-password', APP_HOST='127.0.0.1',
                       PORT=str(port), APP_LLM_MODE='mock', DB_SQLITE_PATH=str(fixture),
                       DB_SCHEMA_JSON=str(root/'schema.json'), GRADIO_ANALYTICS_ENABLED='False',
                       HF_HUB_OFFLINE='1')
            server = subprocess.Popen([sys.executable, '-B', 'app.py'], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            connections = []
            try:
                origin = f'http://127.0.0.1:{port}'
                deadline = time.monotonic()+40
                while True:
                    try:
                        with urllib.request.urlopen(origin+'/health/ready', timeout=1) as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.HTTPError):
                        if time.monotonic() > deadline or server.poll() is not None:
                            self.fail('Serving process did not become ready')
                        time.sleep(.1)
                # Ingress/browser keep-alive connections count against Uvicorn's
                # limit even before a request. The old 16-slot limit rejects this.
                connections = [socket.create_connection(('127.0.0.1', port), timeout=3)
                               for _ in range(32)]
                time.sleep(.1)
                def request(connection):
                    connection.sendall(b'GET /health/live HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                    return connection.recv(4096).split(b'\r\n', 1)[0]
                with ThreadPoolExecutor(max_workers=32) as pool:
                    responses = list(pool.map(request, connections))
                self.assertTrue(all(b'200 OK' in response for response in responses), responses)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(origin+'/config', timeout=3)
                self.assertEqual(denied.exception.code, 401)
            finally:
                for connection in connections:
                    connection.close()
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
