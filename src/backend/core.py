# SPDX-License-Identifier: GPL-3.0-or-later
"""PhoneBridge's local operations. No third-party code runs on import."""
import hashlib
import json
import os
import platform
import plistlib
import re
import selectors
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
import uuid
import zipfile
from pathlib import Path

RESOURCES = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('PHONEBRIDGE_DATA_DIR') or
            Path.home() / 'Library' / 'Application Support' / 'PhoneBridge')
LIBRARY = ROOT / 'Library'
PRIVATE = ROOT / 'private'
PMD_VERSION = '11.43.1'
IPA_VERSION = '2.7.0'
MAX_IPA = 10 * 1024**3


def private_dirs():
    for path in (LIBRARY, PRIVATE):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)


def python_path():
    path = PRIVATE / 'venv' / 'bin' / 'python'
    return str(path) if path.exists() else sys.executable


def ipa_tool():
    arch = 'arm64' if platform.machine() == 'arm64' else 'amd64'
    path = RESOURCES / 'tools' / ('ipatool-' + arch)
    if not path.is_file():
        raise ValueError('Не найден ipatool. Выполните подготовку компонентов.')
    return str(path)


def tool_env():
    env = os.environ.copy()
    env.update({'NO_COLOR': '1', 'PYTHONUNBUFFERED': '1',
                'PYTHONDONTWRITEBYTECODE': '1', 'XDG_STATE_HOME': str(PRIVATE / 'state')})
    return env


def redact(text):
    text = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', str(text))
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[учётная запись]', text)
    text = re.sub(r'(?i)(password|token|cookie|authorization|secret)([\"\s:=]+)[^\s,}]+', r'\1=[скрыто]', text)
    text = re.sub(r'https?://[^\s\"]+\?[^\s\"]+', '[адрес запроса]', text)
    return text[-2400:]


def explain_error(raw):
    lower = raw.lower()
    if '301' in lower or 'redirect' in lower:
        return 'Apple перенаправил запрос входа. Повторите попытку позже или проверьте другую сеть.'
    if '503' in lower or 'body is empty' in lower:
        return 'Сервис входа Apple не вернул ожидаемый ответ. Проверьте другую сеть или повторите попытку позже.'
    if '403' in lower or 'action signature' in lower:
        return 'Apple отклонил запрос. Проверьте версию ipatool и подключение к интернету.'
    if 'not found' in lower or 'not available' in lower:
        return 'Объект недоступен. Для удалённого приложения попробуйте его числовой App Store ID.'
    if 'license' in lower:
        return 'Для приложения не найдена лицензия этой учётной записи. Покупки автоматически не выполняются.'
    if 'not logged' in lower or 'account info' in lower or 'not exist' in lower or 'keyring' in lower:
        return 'Проверьте вход в Apple и доступ ipatool к Связке ключей.'
    return 'Операция не завершена. Технические сведения приведены ниже.'


def json_records(output):
    records = []
    for line in output.splitlines():
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                records.append(data)
        except (ValueError, TypeError):
            pass
    return records


def read_ipa(path):
    path = Path(path)
    if path.suffix.lower() != '.ipa' or not path.is_file():
        raise ValueError('Выберите существующий файл .ipa.')
    if path.stat().st_size > MAX_IPA:
        raise ValueError('Файл больше 10 ГБ. Он не поддерживается первой версией.')
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) > 100000:
                raise ValueError('В архиве слишком много файлов.')
            candidates = [x for x in names if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', x)]
            if len(candidates) != 1:
                raise ValueError('В IPA не найден единственный основной Info.plist.')
            def read_plist(name):
                item = archive.getinfo(name)
                if item.file_size > 4 * 1024**2:
                    raise ValueError('Слишком большой файл метаданных в IPA.')
                obj = plistlib.loads(archive.read(item))
                if not isinstance(obj, dict):
                    raise ValueError('Некорректные метаданные IPA.')
                return obj
            info = read_plist(candidates[0])
            metadata = read_plist('iTunesMetadata.plist') if 'iTunesMetadata.plist' in names else {}
            bundle = info.get('CFBundleIdentifier')
            if not isinstance(bundle, str) or not bundle:
                raise ValueError('В IPA отсутствует идентификатор приложения.')
            return {'name': str(info.get('CFBundleDisplayName') or info.get('CFBundleName') or bundle),
                    'bundle_id': bundle, 'version': str(info.get('CFBundleShortVersionString', '—')),
                    'app_id': metadata.get('itemId') or '',
                    'minimum_ios': str(info.get('MinimumOSVersion', '—')),
                    'size': path.stat().st_size,
                    'has_receipt': any('/SC_Info/' in x and x.endswith('.sinf') for x in names)}
    except (zipfile.BadZipFile, plistlib.InvalidFileException, RuntimeError, KeyError, EOFError) as exc:
        raise ValueError('Файл не является поддерживаемым IPA: ' + str(exc)) from exc


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def library_path(filename):
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.ipa'):
        raise ValueError('Недопустимое имя IPA.')
    path = (LIBRARY / filename).resolve()
    if path.parent != LIBRARY.resolve() or not path.is_file():
        raise ValueError('Файл не найден в библиотеке.')
    return path


def add_ipa(source):
    source = Path(source).resolve(strict=True)
    meta = read_ipa(source)
    digest = file_hash(source)
    stem = re.sub(r'[^A-Za-z0-9._-]', '_', meta['bundle_id'])[:100]
    filename = stem + '-' + digest[:12] + '.ipa'
    destination = LIBRARY / filename
    if not destination.exists():
        temporary = LIBRARY / ('.import-' + uuid.uuid4().hex)
        try:
            with open(source, 'rb') as src, open(temporary, 'xb') as dst:
                os.chmod(temporary, 0o600)
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    meta.update({'filename': filename, 'sha256': digest})
    sidecar = LIBRARY / (filename + '.json')
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    sidecar.chmod(0o600)
    return meta


def list_library():
    result = []
    for path in sorted(LIBRARY.glob('*.ipa')):
        if path.is_symlink():
            continue
        try:
            meta = json.loads(path.with_name(path.name + '.json').read_text(encoding='utf-8'))
            if meta.get('size') != path.stat().st_size:
                meta = read_ipa(path)
            meta['filename'] = path.name
            result.append(meta)
        except (OSError, ValueError):
            try:
                meta = read_ipa(path)
                meta['filename'] = path.name
                result.append(meta)
            except (OSError, ValueError):
                pass
    return result


class Job:
    def __init__(self, title, action):
        self.id = uuid.uuid4().hex
        self.title, self.action = title, action
        self.status = 'running'
        self.events = []
        self.result = None
        self.error = None
        self.prompt = None
        self.process = None
        self.pty_fd = None
        self.cancelled = False
        self.lock = threading.RLock()
        self.created = time.time()

    def event(self, text):
        with self.lock:
            self.events.append({'time': time.strftime('%H:%M:%S'), 'text': redact(text)})
            self.events = self.events[-30:]

    def snapshot(self):
        with self.lock:
            return {'id': self.id, 'title': self.title, 'action': self.action,
                    'status': self.status, 'events': list(self.events), 'result': self.result,
                    'error': self.error, 'prompt': self.prompt,
                    'elapsed_seconds': max(0, int(time.time() - self.created))}

    def cancel(self):
        with self.lock:
            self.cancelled = True
            self.prompt = None
            if self.process and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def answer(self, value):
        with self.lock:
            if not self.prompt or self.pty_fd is None or self.status != 'running':
                raise ValueError('Сейчас нет запроса на ввод.')
            if not isinstance(value, str) or not value or len(value) > 4096 or '\n' in value or '\r' in value:
                raise ValueError('Введите значение в одну строку.')
            if self.prompt['kind'] == 'code' and not re.fullmatch(r'[0-9 ]{6,12}', value):
                raise ValueError('Введите шестизначный код Apple.')
            kind = self.prompt['kind']
            self.prompt = None
            os.write(self.pty_fd, (value + '\n').encode('utf-8'))
            self.event({'email': 'Учётная запись указана. Ожидаем запрос пароля.',
                        'password': 'Пароль передан ipatool. Первый вход может занять несколько минут.',
                        'code': 'Код передан ipatool. Ожидаем результат проверки Apple.',
                        'keychain': 'Пароль хранилища передан ipatool.'}.get(kind, 'Продолжаем вход.'))


class Operations:
    def __init__(self):
        private_dirs()
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, action, data):
        titles = {'devices': 'Поиск iPhone', 'apps': 'Приложения на iPhone',
                  'login': 'Вход в Apple', 'account': 'Проверка входа', 'logout': 'Выход из Apple',
                  'purchases': 'Приложения учётной записи', 'search': 'Поиск в App Store',
                  'download': 'Загрузка IPA', 'install': 'Установка на iPhone',
                  'import': 'Добавление IPA', 'setup': 'Подготовка подключения'}
        if action not in titles:
            raise ValueError('Неизвестная операция.')
        with self.lock:
            if any(j.status == 'running' for j in self.jobs.values()):
                raise ValueError('Дождитесь окончания текущей операции или отмените её.')
            job = Job(titles[action], action)
            self.jobs = dict(list(self.jobs.items())[-19:])
            self.jobs[job.id] = job
        threading.Thread(target=self._execute, args=(job, data), daemon=True).start()
        return job

    def _execute(self, job, data):
        try:
            job.event(job.title)
            result = self.perform(job, data)
            with job.lock:
                job.status = 'cancelled' if job.cancelled else 'done'
                job.result = None if job.cancelled else result
                job.prompt = None
            job.event('Операция отменена.' if job.cancelled else 'Готово.')
        except Exception as exc:
            raw = str(exc)
            with job.lock:
                job.status = 'cancelled' if job.cancelled else 'error'
                job.error = {'message': explain_error(raw), 'details': redact(raw)}
                job.prompt = None
            job.event('Операция отменена.' if job.cancelled else 'Операция не завершена.')

    def run(self, job, command, timeout=180):
        if job.cancelled:
            raise RuntimeError('Отменено.')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=tool_env(), start_new_session=True)
        with job.lock:
            job.process = process
            if job.cancelled:
                job.cancel()
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise RuntimeError('Истекло время ожидания операции.')
        finally:
            with job.lock:
                job.process = None
        output = stdout.decode('utf-8', 'replace')
        errors = stderr.decode('utf-8', 'replace')
        if process.returncode:
            records = json_records(output)
            messages = [str(x.get('error', '')) for x in records if x.get('error')]
            raise RuntimeError('\n'.join(messages) or errors or output or 'Команда завершилась с ошибкой.')
        return output

    def store(self, job, *args, timeout=180):
        output = self.run(job, [ipa_tool(), '--non-interactive', '--format', 'json', *args], timeout)
        records = json_records(output)
        if not records:
            raise RuntimeError('ipatool не вернул ожидаемый JSON-ответ.')
        for record in records:
            if record.get('success') is False:
                raise RuntimeError(record.get('error') or 'Ошибка ipatool.')
        return records[-1]

    def device(self, job, action, data):
        if not (PRIVATE / 'venv' / '.ready').is_file():
            raise ValueError('Подготовьте компоненты подключения в разделе «О приложении».')
        command = [python_path(), str(RESOURCES / 'device_worker.py'), action]
        if action != 'devices':
            udid = data.get('udid', '')
            if not isinstance(udid, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,80}', udid):
                raise ValueError('Выберите конкретный подключённый iPhone.')
            command += ['--udid', udid]
        if action == 'install':
            command += ['--ipa', str(library_path(data.get('filename')))]
        out = self.run(job, command, timeout=1200 if action == 'install' else 90)
        return json.loads(out)

    def perform(self, job, data):
        action = job.action
        if action == 'setup':
            return self.setup(job)
        if action in ('devices', 'apps'):
            return self.device(job, action, data)
        if action == 'install':
            path = library_path(data.get('filename'))
            read_ipa(path)
            job.event('Установка на выбранный iPhone. Оставьте телефон подключённым и разблокированным.')
            return self.device(job, 'install', data)
        if action == 'login':
            return self.login(job)
        if action == 'account':
            info = self.store(job, 'auth', 'info')
            return {k: info.get(k, '') for k in ('name', 'email', 'success')}
        if action == 'logout':
            return self.store(job, 'auth', 'revoke')
        if action == 'purchases':
            page = int(data.get('page', 1))
            if not 1 <= page <= 10000:
                raise ValueError('Недопустимый номер страницы.')
            return self.store(job, 'list-purchases', '--page', str(page), '--max-results', '100', '--platform', 'iphone')
        if action == 'search':
            term = str(data.get('term', '')).strip()
            if not 1 <= len(term) <= 120:
                raise ValueError('Введите название приложения.')
            return self.store(job, 'search', term, '--limit', '25', '--platform', 'iphone')
        if action == 'import':
            return add_ipa(data.get('path', ''))
        if action == 'download':
            app_id = str(data.get('app_id', '')).strip()
            bundle = str(data.get('bundle_id', '')).strip()
            if not app_id.isdigit() and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,180}', bundle):
                raise ValueError('Укажите числовой App Store ID или идентификатор приложения.')
            if not app_id.isdigit():
                app_id = self.resolve_owned_id(job, bundle)
            temporary = PRIVATE / ('download-' + uuid.uuid4().hex + '.ipa')
            command = ['download', '--platform', 'iphone', '--output', str(temporary)]
            # A numeric ID avoids a public-store lookup for delisted apps.
            command += ['--app-id', app_id] if app_id.isdigit() else ['--bundle-identifier', bundle]
            version_id = str(data.get('version_id', '')).strip()
            if version_id:
                if not version_id.isdigit():
                    raise ValueError('ID версии должен содержать только цифры.')
                command += ['--external-version-id', version_id]
            job.event('Запрос к Apple. Загружаем приложение по лицензии текущей учётной записи.')
            try:
                output = self.store(job, *command, timeout=1800)
                destination = Path(output.get('output', str(temporary))).resolve()
                if destination != temporary.resolve():
                    raise RuntimeError('ipatool вернул неожиданный путь файла.')
                metadata = read_ipa(destination)
                if bundle and metadata['bundle_id'] != bundle:
                    raise ValueError('Apple вернул другое приложение. Его идентификатор: ' + metadata['bundle_id'])
                return add_ipa(destination)
            finally:
                temporary.unlink(missing_ok=True)
        raise ValueError('Неизвестная операция.')

    def resolve_owned_id(self, job, bundle):
        """Resolve delisted app IDs from owned apps before trying public lookup."""
        seen = set()
        for page in range(1, 101):
            if job.cancelled:
                raise RuntimeError('Отменено.')
            job.event('Поиск приложения в истории учётной записи: страница ' + str(page) + '.')
            response = self.store(job, 'list-purchases', '--page', str(page),
                                  '--max-results', '100')
            apps = response.get('apps', [])
            for app in apps:
                if (app.get('bundleID') or app.get('bundleId')) == bundle:
                    app_id = str(app.get('id') or app.get('trackId') or '')
                    if app_id.isdigit():
                        return app_id
            signature = tuple(str(app.get('id') or app.get('trackId') or app.get('bundleID') or '') for app in apps)
            if not apps or signature in seen or page * 100 >= int(response.get('totalCount') or len(apps)):
                break
            seen.add(signature)
        job.event('В истории ID не найден. Проверяем идентификатор приложения через Apple.')
        return ''

    def setup(self, job):
        venv_path = PRIVATE / 'venv'
        marker = venv_path / '.ready'
        if marker.exists():
            return {'ready': True, 'version': PMD_VERSION}
        job.event('Создание отдельного окружения Python.')
        self.run(job, [sys.executable, '-m', 'venv', str(venv_path)], timeout=180)
        job.event('Получение pymobiledevice3 с PyPI. Первая подготовка может занять несколько минут.')
        self.run(job, [str(venv_path / 'bin' / 'python'), '-m', 'pip', '--disable-pip-version-check',
                       'install', '--no-input', '--index-url', 'https://pypi.org/simple',
                       'pymobiledevice3==' + PMD_VERSION], timeout=1200)
        self.run(job, [str(venv_path / 'bin' / 'python'), '-c', 'import pymobiledevice3'], timeout=30)
        marker.write_text(PMD_VERSION, encoding='utf-8')
        return {'ready': True, 'version': PMD_VERSION}

    def login(self, job):
        import pty
        master, slave = pty.openpty()
        settings = termios.tcgetattr(slave)
        settings[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, settings)
        selector = selectors.DefaultSelector()
        process = None
        records, buffer = [], ''
        try:
            process = subprocess.Popen([ipa_tool(), '--format', 'json', 'auth', 'login'],
                                       stdin=slave, stdout=slave, stderr=slave, env=tool_env(),
                                       start_new_session=True, close_fds=True)
            os.close(slave)
            slave = None
            with job.lock:
                job.process, job.pty_fd = process, master
                if job.cancelled:
                    job.cancel()
            selector.register(master, selectors.EVENT_READ)
            start = time.monotonic()
            last_progress = start
            while True:
                now = time.monotonic()
                if now - start > 1200:
                    raise RuntimeError('Время входа истекло. Начните вход заново.')
                if now - last_progress > 30:
                    with job.lock:
                        if job.prompt is None and not job.cancelled:
                            job.event('Вход выполняется. Подготовка первого входа и ответ Apple могут занять несколько минут.')
                    last_progress = now
                ready = selector.select(timeout=0.2)
                if ready:
                    try:
                        chunk = os.read(master, 8192)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buffer = (buffer + chunk.decode('utf-8', 'replace'))[-32768:]
                    prompts = [
                        ('enter Apple ID (email address or phone number): ', 'email', 'Учётная запись Apple'),
                        ('enter password: ', 'password', 'Пароль учётной записи Apple'),
                        ('enter 2FA code: ', 'code', 'Код подтверждения Apple'),
                    ]
                    for marker, kind, label in prompts:
                        if marker in buffer:
                            buffer = buffer.split(marker, 1)[1]
                            with job.lock:
                                job.prompt = {'kind': kind, 'label': label, 'id': uuid.uuid4().hex}
                    if 'enter passphrase to unlock ' in buffer and '): ' in buffer:
                        buffer = buffer.split('): ', 1)[1]
                        with job.lock:
                            job.prompt = {'kind': 'keychain', 'label': 'Пароль зашифрованного хранилища ipatool', 'id': uuid.uuid4().hex}
                    fresh = json_records(buffer)
                    records += fresh
                    if any('preparing authentication' in str(record.get('message', '')) for record in fresh):
                        job.event('Подготовка входа в Apple. При первом запуске ipatool получает служебные компоненты.')
                    # Keep only incomplete content. Never expose terminal text to the UI.
                    if '\n' in buffer:
                        buffer = buffer.rsplit('\n', 1)[1]
                if process.poll() is not None and not ready:
                    break
            process.wait(timeout=5)
            for record in reversed(records):
                if record.get('success') is True:
                    return {k: record.get(k, '') for k in ('name', 'email', 'success')}
            errors = [str(r.get('error')) for r in records if r.get('error')]
            raise RuntimeError(errors[-1] if errors else 'Вход не завершён. Повторите вход и проверьте окно Связки ключей macOS.')
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            selector.close()
            if slave is not None:
                os.close(slave)
            os.close(master)
            with job.lock:
                job.pty_fd, job.process, job.prompt = None, None, None
