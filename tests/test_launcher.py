import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
from unittest.mock import patch

import launcher


def test_existing_service_and_unrelated_port_are_distinguished():
    class Handler(BaseHTTPRequestHandler):
        payload = {'ok': True, 'backend': 'original-musicdl', 'sources': 57}
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(self.payload).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert launcher.service_status('0.0.0.0', server.server_port)[0] == 'running'
        Handler.payload = {'ok': True, 'backend': 'rust'}
        assert launcher.service_status('127.0.0.1', server.server_port)[0] == 'old-version'
        Handler.payload = {'ok': True, 'service': 'unrelated'}
        assert launcher.service_status('127.0.0.1', server.server_port)[0] == 'occupied'
    finally:
        server.shutdown()
        server.server_close()
    assert launcher.service_status('127.0.0.1', server.server_port)[0] == 'available'


def test_duplicate_launch_does_not_spawn_or_import_app(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    monkeypatch.setenv('MUSICDL_PORT', '8765')
    monkeypatch.setenv('MUSICDL_NO_BROWSER', '1')
    with patch.object(launcher, 'service_status', return_value=('running', 'http://127.0.0.1:8765')):
        with patch.object(launcher.subprocess, 'Popen') as spawn:
            assert launcher.main() == 0
            spawn.assert_not_called()
    assert '已在运行' in (tmp_path / 'logs/startup.log').read_text(encoding='utf-8')
