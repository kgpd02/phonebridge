# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback-only application server. All API calls require a per-launch secret."""
import argparse
import hmac
import json
import mimetypes
import os
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from core import Operations, RESOURCES, ROOT, LIBRARY, PRIVATE, IPA_VERSION, PMD_VERSION, list_library

TOKEN = secrets.token_urlsafe(32)
OPS = Operations()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Request bodies and account input must never appear in logs.

    def reply(self, data, status=200, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def trusted(self, api=False):
        expected = '127.0.0.1:' + str(self.server.server_port)
        if self.headers.get('Host') != expected:
            self.reply({'error': 'Недопустимый адрес сервера.'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + expected:
            self.reply({'error': 'Запрос с другого сайта запрещён.'}, 403)
            return False
        if api and not hmac.compare_digest(self.headers.get('X-PhoneBridge-Token', ''), TOKEN):
            self.reply({'error': 'Перезапустите приложение для новой сессии.'}, 403)
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.trusted(path.startswith('/api/')):
            return
        try:
            if path == '/api/status':
                self.reply({'version': '0.1.0', 'ipatool': IPA_VERSION,
                            'device_engine': PMD_VERSION,
                            'device_ready': (PRIVATE / 'venv' / '.ready').exists(),
                            'library_count': len(list_library()),
                            'library_path': str(LIBRARY),
                            'jobs': [job.snapshot() for job in OPS.jobs.copy().values()]})
            elif path == '/api/library':
                self.reply({'files': list_library()})
            elif path.startswith('/api/jobs/'):
                job = OPS.jobs.get(path.rsplit('/', 1)[-1])
                self.reply(job.snapshot() if job else {'error': 'Операция не найдена.'}, 200 if job else 404)
            else:
                name = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(path)
                if not name:
                    self.reply({'error': 'Страница не найдена.'}, 404)
                    return
                file = RESOURCES / 'web' / name
                self.reply(file.read_bytes(), content_type=mimetypes.guess_type(name)[0] + '; charset=utf-8')
        except (ValueError, OSError) as exc:
            self.reply({'error': str(exc)}, 400)

    def do_POST(self):
        if not self.trusted(api=True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                self.reply({'error': 'Недопустимый размер запроса.'}, 413)
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply({'error': 'Ожидается JSON.'}, 415)
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Ожидается объект JSON.')
            path = urlsplit(self.path).path
            if path == '/api/actions':
                job = OPS.start(data.get('action'), data)
                self.reply({'job_id': job.id}, 202)
            elif path == '/api/answer':
                job = OPS.jobs.get(data.get('job_id'))
                if not job:
                    raise ValueError('Операция не найдена.')
                job.answer(data.get('value'))
                self.reply({'ok': True})
            elif path == '/api/cancel':
                job = OPS.jobs.get(data.get('job_id'))
                if not job:
                    raise ValueError('Операция не найдена.')
                job.cancel()
                self.reply({'ok': True})
            elif path == '/api/heartbeat':
                self.server.last_seen = time.monotonic()
                self.reply({'ok': True})
            else:
                self.reply({'error': 'Неизвестная команда.'}, 404)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.reply({'error': str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-idle-exit', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.last_seen = time.monotonic()
    def stop(*_):
        for job in list(OPS.jobs.values()):
            job.cancel()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not args.no_idle_exit:
        def watchdog():
            while True:
                time.sleep(10)
                if time.monotonic() - server.last_seen > 90:
                    stop()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
    print(json.dumps({'url': 'http://127.0.0.1:' + str(server.server_port) + '/#' + TOKEN}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
