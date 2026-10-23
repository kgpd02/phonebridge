#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create a drag-to-Applications DMG from the clean build output."""
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'


def main():
    app = DIST / 'PhoneBridge.app'
    with (app / 'Contents/Info.plist').open('rb') as stream:
        version = plistlib.load(stream)['CFBundleShortVersionString']
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    stage = ROOT / 'build/dmg'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(app, stage / app.name, symlinks=True)
    (stage / 'Applications').symlink_to('/Applications')
    shutil.copy2(ROOT / 'docs/INSTALL.ru.md', stage / 'Установка.txt')
    target = DIST / f'PhoneBridge-{version}-macos-universal.dmg'
    subprocess.run(['hdiutil', 'create', '-ov', '-volname', 'PhoneBridge ' + version,
                    '-srcfolder', str(stage), '-format', 'UDZO', str(target)], check=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (DIST / (target.name + '.sha256')).write_text(digest + '  ' + target.name + '\n')
    print('Packaged:', target)


if __name__ == '__main__':
    main()
