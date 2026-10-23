# SPDX-License-Identifier: GPL-3.0-or-later
"""A small, separately executed adapter for the public pymobiledevice3 API."""
import argparse
import asyncio
import json
import plistlib
import sys


def app_summary(bundle, app):
    metadata = app.get('iTunesMetadata') or {}
    if isinstance(metadata, bytes):
        try:
            metadata = plistlib.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    app_id = app.get('AdamID') or app.get('iTunesStoreItemIdentifier') or metadata.get('itemId') or ''
    return {'name': str(app.get('CFBundleDisplayName') or app.get('CFBundleName') or bundle),
            'bundle_id': bundle, 'version': str(app.get('CFBundleShortVersionString', '—')),
            'app_id': str(app_id),
            'placeholder': bool(app.get('IsPlaceholder') or app.get('Placeholder'))}


async def main(args):
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    if args.action == 'devices':
        result = []
        for device in await list_devices():
            if not device.is_usb:
                continue
            row = {'udid': device.serial, 'name': 'iPhone', 'ios': '', 'model': '', 'paired': False}
            lockdown = None
            try:
                lockdown = await asyncio.wait_for(create_using_usbmux(serial=device.serial, autopair=False), 5)
                values = await asyncio.wait_for(lockdown.get_value(), 5)
                row.update({'name': values.get('DeviceName', 'iPhone'),
                            'ios': values.get('ProductVersion', ''),
                            'model': values.get('ProductType', ''), 'paired': bool(lockdown.paired)})
            except Exception:
                row['hint'] = 'Разблокируйте iPhone. При получении списка приложений подтвердите «Доверять».'
            finally:
                if lockdown:
                    await lockdown.close()
            result.append(row)
        return result
    lockdown = await create_using_usbmux(serial=args.udid, autopair=True, pair_timeout=45)
    try:
        async with InstallationProxyService(lockdown) as service:
            if args.action == 'apps':
                apps = await service.get_apps(application_type='User', show_placeholders=True)
                # iOS omits store metadata unless these attributes are requested explicitly.
                metadata_apps = await service.lookup({'ApplicationType': 'User',
                    'ReturnAttributes': ['CFBundleIdentifier', 'iTunesMetadata',
                                         'AdamID', 'iTunesStoreItemIdentifier']})
                for bundle, metadata_app in (metadata_apps or {}).items():
                    if bundle in apps:
                        apps[bundle].update(metadata_app)
                return sorted([app_summary(bundle, app) for bundle, app in apps.items()],
                              key=lambda item: item['name'].casefold())
            if args.action == 'install':
                # No developer re-signing, erasure, backup restore, or removal.
                await service.install_from_local(args.ipa)
                return {'installed': True, 'udid': args.udid}
    finally:
        await lockdown.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['devices', 'apps', 'install'])
    parser.add_argument('--udid')
    parser.add_argument('--ipa')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(main(args)), ensure_ascii=False))
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        sys.exit(1)
