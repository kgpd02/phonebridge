# PhoneBridge: from a migration problem to a macOS product

**Builder:** [kgpd02 · Product Builder](https://github.com/kgpd02)

**Release:** 0.1.0, early access

**Implementation:** AI-assisted, using existing open-source Apple device tooling.

## The problem

An old iPhone still had apps that no longer appeared in App Store search. A new phone needed those apps. The existing Mac tool failed during Apple authentication. The user needed a usable way to preserve the eligible app packages before moving devices.

The initial assumption was that an installed app could simply be copied from one iPhone to another. Modern iOS does not provide a general export path for an installable IPA. That constraint changed the product: read the old phone's app metadata, then request the original package from Apple under the owner's account.

## My contribution

I set the problem and the product direction, used AI-assisted implementation to build the macOS app, and tested the flow with a real iPhone and Apple Account. My feedback drove the login, verification-code and download fixes.

The technical foundation comes from ipatool and pymobiledevice3. PhoneBridge brings those tools into one local workflow. It does not claim a new Apple authentication protocol or a new way to bypass licensing.

## Product decisions

| Constraint | Decision | User outcome |
| --- | --- | --- |
| App Store search can omit a delisted app. | Read its numeric store ID from iPhone metadata. | An eligible package can be requested without public search. |
| An app name is not a unique identity. | Verify the downloaded bundle identifier. | A different app is rejected before library import. |
| Apple sign-in can take several minutes. | Add elapsed time, progress updates and cancellation. | The user can see whether work is still running. |
| Several phones can be connected. | Show and confirm the target before installation. | The user can check the selected device. |
| Private credentials and files are involved. | Keep the UI local and disclose Keychain storage. | The data flow and storage are explicit. |
| A developer's Python environment is not an installer. | Bundle Python and store mutable data in Application Support. | The released app can live in Applications. |

## The decisive debugging result

The first download attempt failed with `app not found`. Searching the account's returned purchase history did not identify the selected apps. This did not establish that the account lacked a license.

A second iPhone metadata request explicitly asked for `iTunesMetadata`. That request returned the numeric App Store IDs that the default inventory omitted. Downloads by those IDs succeeded. Both package versions and bundle identifiers matched the apps on the old phone.

This became a product fix, not a manual instruction to paste a guessed ID: the inventory adapter now requests the metadata and fills the download form automatically.

## Evidence and limits

The original device inventory returned 108 installed apps. Two selected delisted apps were downloaded from Apple. Real sign-in and two-factor authentication completed. Automated checks exercise the local API boundary, IPA validation, credential handling and command construction.

The new-device installation path has not yet been validated on a second physical iPhone. The release says so. Neither download success nor the presence of an Install button establishes successful launch on a new device.

## What this project demonstrates

- Start with a concrete problem and test the initial assumptions.
- Use existing tools where they are strong.
- Turn debugging evidence into a simpler user flow.
- Make data handling and product limits visible.
- Ship source, a macOS installer and a reproducible build path together.

## Next validation

1. Complete a transfer to a second physical iPhone.
2. Check installation and first launch separately.
3. Test a physical Intel Mac.
4. Add Developer ID signing and notarization when a signing identity is available.
5. Improve setup and recovery from Apple service errors using observed failures.
