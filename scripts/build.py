#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a clean universal macOS app using pinned, verified dependencies."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
DIST = ROOT / 'dist'
MANIFEST = json.loads((ROOT / 'third_party/runtime-manifest.json').read_text())


def run(*args):
    subprocess.run([str(x) for x in args], check=True)


def fetch(item):
    cache = BUILD / 'downloads' / (item['sha256'] + '.tar.gz')
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and hashlib.sha256(cache.read_bytes()).hexdigest() == item['sha256']:
        return cache
    print('Downloading', item['url'].rsplit('/', 1)[-1], flush=True)
    request = urllib.request.Request(item['url'], headers={'User-Agent': 'PhoneBridge-build'})
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise RuntimeError('SHA-256 mismatch: ' + item['url'])
    cache.write_bytes(data)
    return cache


def main():
    if sys.version_info < (3, 12):
        raise SystemExit('Build requires Python 3.12 or newer.')
    if sys.platform != 'darwin':
        raise SystemExit('Build requires macOS and Xcode Command Line Tools.')
    BUILD.mkdir(exist_ok=True)
    DIST.mkdir(exist_ok=True)
    dependencies = [(kind, arch, item) for kind in ('python', 'ipatool')
                    for arch, item in MANIFEST[kind]['archives'].items()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(fetch, (x[2] for x in dependencies)))
    app = DIST / 'PhoneBridge.app'
    if app.exists():
        shutil.rmtree(app)
    resources = app / 'Contents/Resources'
    executable = app / 'Contents/MacOS/PhoneBridge'
    executable.parent.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copy2(ROOT / 'src/native/Info.plist', app / 'Contents/Info.plist')
    for file in (ROOT / 'src/backend').glob('*.py'):
        shutil.copy2(file, resources / file.name)
    shutil.copytree(ROOT / 'src/web', resources / 'web')
    shutil.copytree(ROOT / 'third_party', resources / 'third_party')
    shutil.copy2(ROOT / 'LICENSE', resources / 'LICENSE')
    for (kind, arch, _), archive in zip(dependencies, fetched):
        if kind == 'python':
            with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
                with tarfile.open(archive) as tar:
                    tar.extractall(temporary, filter='data')
                destination = resources / 'runtime' / arch
                destination.parent.mkdir(exist_ok=True)
                shutil.copytree(Path(temporary) / 'python', destination, symlinks=True)
        else:
            with tarfile.open(archive) as tar:
                candidates = [m for m in tar.getmembers() if m.isfile() and '/ipatool-' in m.name]
                if len(candidates) != 1:
                    raise RuntimeError('Unexpected ipatool archive layout')
                destination = resources / 'tools' / ('ipatool-' + ('amd64' if arch == 'x86_64' else arch))
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(tar.extractfile(candidates[0]).read())
                destination.chmod(0o755)
    for arch in ('arm64', 'x86_64'):
        print('Compiling native launcher:', arch, flush=True)
        run('swiftc', '-O', '-target', arch + '-apple-macosx13.0', '-framework', 'AppKit',
            '-framework', 'WebKit', '-framework', 'UniformTypeIdentifiers',
            ROOT / 'src/native/Launcher.swift', '-o', BUILD / ('PhoneBridge-' + arch))
    run('lipo', '-create', BUILD / 'PhoneBridge-arm64', BUILD / 'PhoneBridge-x86_64', '-output', executable)
    # Sign modified bundle locally. This is not a Developer ID signature or notarization.
    run('codesign', '--force', '--deep', '--sign', '-', app)
    run('codesign', '--verify', '--deep', '--strict', app)
    print('Built:', app, flush=True)


if __name__ == '__main__':
    main()
