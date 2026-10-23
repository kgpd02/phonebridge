# Contributing

PhoneBridge is an early-access macOS product. Small, reproducible fixes are welcome.

## Source layout

- `src/native/`: AppKit launcher and bundle metadata.
- `src/backend/`: localhost API, app operations and device adapter.
- `src/web/`: local WebKit interface.
- `tests/`: backend and boundary tests using temporary fixtures.
- `scripts/`: verified dependency fetching, native build and DMG packaging.
- `docs/`: installation, product case study and architecture.
- `third_party/`: notices, pinned manifests and upstream source archives.

## Develop

Use Python 3.12 or newer. Backend checks do not require an iPhone, Apple account or pymobiledevice3 installation:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

For a browser-based development session, copy the three files in `src/web/` into `src/backend/web/` in a temporary build directory, or build the app first. Run the assembled backend:

```sh
PHONEBRIDGE_DATA_DIR="$PWD/build/dev-data" \
  dist/PhoneBridge.app/Contents/Resources/runtime/arm64/bin/python3.12 \
  dist/PhoneBridge.app/Contents/Resources/server.py --no-idle-exit
```

Use `x86_64` in the runtime path on Intel. The server prints a localhost URL containing a session token. Keep that URL private.

## Review expectations

Describe the user-visible problem, resulting behavior and validation. Keep authentication behavior explicit. Never add account credentials, device inventories, IPA files, cookies or production logs to a commit or issue.

Do not add automatic purchases, device erasure or silent installation on a default device. Changes to the authentication flow need particular care around logging and subprocess arguments.

A macOS CI template is available at `.github/checks.example.yml`. Move it to `.github/workflows/checks.yml` to enable GitHub Actions when your publishing credential has workflow permissions.
