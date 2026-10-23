# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

RESOURCES = Path(__file__).resolve().parents[1] / 'src' / 'backend'
sys.path.insert(0, str(RESOURCES))
import core
import device_worker


def fixture_ipa(path, name='Sample App', bundle='com.example.sample'):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('Payload/Sample.app/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': bundle, 'CFBundleDisplayName': name,
            'CFBundleShortVersionString': '1.2', 'MinimumOSVersion': '16.0'}))
        archive.writestr('iTunesMetadata.plist', plistlib.dumps({'itemId': 123456789}))
        archive.writestr('Payload/Sample.app/SC_Info/Sample.sinf', b'test-receipt')


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'Library'
        self.library.mkdir()
        self.change = patch.object(core, 'LIBRARY', self.library)
        self.change.start()
    def tearDown(self):
        self.change.stop(); self.temp.cleanup()
    def test_inspect_and_import_is_non_destructive_and_deduplicated(self):
        source = self.root / 'source.ipa'; fixture_ipa(source)
        original = source.read_bytes()
        a = core.add_ipa(source); b = core.add_ipa(source)
        self.assertEqual(a['app_id'], 123456789)
        self.assertEqual(a['filename'], b['filename'])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(core.list_library()), 1)
        self.assertEqual((self.library / a['filename']).stat().st_mode & 0o777, 0o600)
    def test_version_identifier_is_not_confused_with_app_id(self):
        path = self.root / 'source.ipa'; fixture_ipa(path)
        with zipfile.ZipFile(path, 'a') as archive:
            # No numeric itemId: never use a build's identifier as the app identifier.
            archive.writestr('other.plist', plistlib.dumps({'softwareVersionExternalIdentifier': 999}))
        self.assertEqual(core.read_ipa(path)['app_id'], 123456789)
    def test_rejects_missing_main_app_and_large_metadata(self):
        bad = self.root / 'bad.ipa'
        with zipfile.ZipFile(bad, 'w') as archive: archive.writestr('../evil', 'data')
        with self.assertRaises(ValueError): core.read_ipa(bad)
        with zipfile.ZipFile(bad, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Payload/X.app/Info.plist', b'0' * (4 * 1024**2 + 1))
        with self.assertRaises(ValueError): core.read_ipa(bad)
    def test_rejects_traversal_and_symlink(self):
        source = self.root / 'source.ipa'; fixture_ipa(source)
        with self.assertRaises(ValueError): core.library_path('../source.ipa')
        (self.library / 'linked.ipa').symlink_to(source)
        with self.assertRaises(ValueError): core.library_path('linked.ipa')
        self.assertEqual(core.list_library(), [])
    def test_app_names_remain_literal_data(self):
        source = self.root / 'source.ipa'; fixture_ipa(source, '<img src=x onerror=alert(1)>')
        self.assertEqual(core.add_ipa(source)['name'], '<img src=x onerror=alert(1)>')


class OperationTests(unittest.TestCase):
    def test_store_id_is_read_from_binary_device_metadata(self):
        result = device_worker.app_summary('com.example.app', {
            'CFBundleShortVersionString': '17.6.1',
            'iTunesMetadata': plistlib.dumps({'itemId': 6769745089,
                                             'softwareVersionExternalIdentifier': 887019421})})
        self.assertEqual(result['app_id'], '6769745089')
    def test_delisted_app_id_is_resolved_from_later_history_page(self):
        operations = core.Operations()
        job = core.Job('Lookup', 'download')
        pages = [
            {'apps': [{'id': 10, 'bundleID': 'other.app'}], 'totalCount': 200},
            {'apps': [{'id': 12345, 'bundleID': 'com.example.removed'}], 'totalCount': 200}]
        with patch.object(operations, 'store', side_effect=pages) as mocked:
            self.assertEqual(operations.resolve_owned_id(job, 'com.example.removed'), '12345')
            self.assertEqual(mocked.call_count, 2)
            self.assertNotIn('--platform', mocked.call_args.args)
    def test_repeated_history_page_does_not_loop_forever(self):
        operations = core.Operations()
        page = {'apps': [{'id': 10, 'bundleID': 'other.app'}], 'totalCount': 99999}
        with patch.object(operations, 'store', return_value=page) as mocked:
            self.assertEqual(operations.resolve_owned_id(core.Job('x', 'download'), 'missing.app'), '')
            self.assertEqual(mocked.call_count, 2)
    def test_download_uses_numeric_id_without_purchase_or_password_arguments(self):
        job = core.Job('Download', 'download')
        calls = []
        operations = core.Operations()
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'download.ipa'
            def store(job, *args, **kwargs):
                calls.append(args)
                path = Path(args[args.index('--output') + 1]); fixture_ipa(path)
                return {'success': True, 'output': str(path)}
            with patch.object(core, 'PRIVATE', Path(folder)), patch.object(operations, 'store', store), patch.object(core, 'add_ipa', lambda x: {'ok': True}):
                result = operations.perform(job, {'app_id': '123456789', 'bundle_id': 'com.example.sample'})
            self.assertTrue(result['ok'])
            self.assertIn('--app-id', calls[0])
            self.assertNotIn('--bundle-identifier', calls[0])
            self.assertNotIn('--purchase', calls[0])
            self.assertNotIn('--password', calls[0])
    def test_download_rejects_wrong_bundle_before_import(self):
        operations = core.Operations()
        with tempfile.TemporaryDirectory() as folder:
            def store(job, *args, **kwargs):
                path = Path(args[args.index('--output') + 1]); fixture_ipa(path)
                return {'success': True, 'output': str(path)}
            with patch.object(core, 'PRIVATE', Path(folder)), patch.object(operations, 'store', store), patch.object(core, 'add_ipa') as imported:
                with self.assertRaisesRegex(ValueError, 'другое приложение'):
                    operations.perform(core.Job('x', 'download'), {'app_id': '123456789', 'bundle_id': 'com.wrong.app'})
                imported.assert_not_called()
    def test_unknown_action_and_missing_target_rejected(self):
        operations = core.Operations()
        with self.assertRaises(ValueError): operations.start('erase', {})
        with patch.object(core.Path, 'is_file', return_value=True):
            with self.assertRaises(ValueError): operations.device(core.Job('x', 'apps'), 'apps', {'udid': ''})
    def test_error_output_redacts_email_and_credentials(self):
        safe = core.redact('user@example.com password=secret token=123 cookie=456')
        for value in ['user@example.com', 'secret', '123', '456']: self.assertNotIn(value, safe)
    def test_interactive_login_password_and_code_never_enter_events_or_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / 'fake-ipatool'
            tool.write_text('#!' + sys.executable + '\n' + '''import sys,json
for prompt in ['enter Apple ID (email address or phone number): ', 'enter password: ', 'enter 2FA code: ']:
    sys.stderr.write(prompt);sys.stderr.flush();value=sys.stdin.readline()
print(json.dumps({'name':'Test','email':'user@example.com','success':True}),flush=True)
''', encoding='utf-8'); tool.chmod(0o700)
            job = core.Job('Login', 'login'); operations = core.Operations(); result = []
            with patch.object(core, 'ipa_tool', return_value=str(tool)):
                thread = threading.Thread(target=lambda: result.append(operations.login(job)))
                thread.start()
                for kind, value in [('email', 'user@example.com'), ('password', 'test-secret-789'), ('code', '123456')]:
                    deadline = time.monotonic() + 5
                    while not job.prompt and time.monotonic() < deadline: time.sleep(.02)
                    self.assertEqual(job.prompt['kind'], kind)
                    self.assertNotIn('test-secret-789', repr(job.process.args))
                    job.answer(value)
                thread.join(5)
                self.assertFalse(thread.is_alive())
            self.assertTrue(result[0]['success'])
            self.assertNotIn('test-secret-789', json.dumps(job.events))
            self.assertNotIn('123456', json.dumps(job.events))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen([sys.executable, str(RESOURCES / 'server.py'), '--no-idle-exit'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info = json.loads(cls.process.stdout.readline())
        cls.base, cls.token = info['url'].split('/#')
    @classmethod
    def tearDownClass(cls):
        cls.process.terminate(); cls.process.wait(timeout=10)
        cls.process.stdout.close(); cls.process.stderr.close()
    def test_api_requires_token_and_rejects_other_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.base + '/api/status')
        self.assertEqual(context.exception.code, 403)
        request = urllib.request.Request(self.base + '/api/status', headers={'X-PhoneBridge-Token': self.token})
        with urllib.request.urlopen(request) as response: self.assertEqual(response.status, 200)
        request = urllib.request.Request(self.base + '/api/heartbeat', data=b'{"alive":true}', headers={
            'X-PhoneBridge-Token': self.token, 'Origin': 'https://attacker.invalid', 'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as context: urllib.request.urlopen(request)
        self.assertEqual(context.exception.code, 403)
    def test_unlisted_files_are_not_served(self):
        for path in ['/core.py', '/private/venv/pyvenv.cfg', '/../../server.py']:
            with self.assertRaises(urllib.error.HTTPError) as context: urllib.request.urlopen(self.base + path)
            self.assertEqual(context.exception.code, 404)


if __name__ == '__main__': unittest.main(verbosity=2)
