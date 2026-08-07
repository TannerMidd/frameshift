# Offline release signing

Frameshift authenticates automatic updates with an Ed25519-signed manifest.
This is free, uses the open-source `cryptography` package, and requires no
commercial Windows code-signing certificate or signing service.

The long-lived private key stays on owner-controlled offline storage. GitHub
Actions receives only executable candidates, a signed manifest, and its public
signature. The app embeds the public key and verifies the exact manifest bytes,
release version, executable names, sizes, and SHA-256 digests before offering or
installing an update.

## One-time key setup

For a brand-new trust root, generate the key directly on private storage that
will remain offline. The confirmation flag is deliberate: the tool creates the
file exclusively and refuses to write private bytes unless owner-only POSIX
permissions or a restricted Windows ACL can be established.

```text
python tools/generate_update_signing_key.py X:\private\frameshift-release-key.txt --confirm-private-storage
```

Copy only the printed public key into `PUBLIC_KEY_B64` in
`elite/update_trust.py`. Keep an encrypted offline backup of the private-key
file accessible only to the project owner.

For the existing Frameshift key, do **not** run the generator again. Move its
matching private-key file to offline storage, restrict the existing file to the
owner (`chmod 600` on POSIX; remove inherited ACLs and grant only the current
account on Windows), and confirm its derived public key still matches
`PUBLIC_KEY_B64` by performing a local signing dry run before deleting any
copy.

If the obsolete `FRAMESHIFT_UPDATE_SIGNING_KEY` repository secret exists,
delete it after confirming the offline backup. The release workflows neither
request nor consume that secret.

Never paste the private key into an issue, log, commit, release asset, workflow,
or chat. Do not generate a replacement casually: installed clients trust the
current public key, so rotation requires an explicitly staged compatibility
release signed by the existing key.

## Release process

1. Push a `vMAJOR.MINOR.PATCH` tag. The `Release` workflow builds and tests both
   executable names, then creates an **unpublished draft** containing the two
   executables and their convenience checksums. It has no access to the private
   key.
2. Download `Frameshift.exe` and `EliteTrader.exe` from that draft and transfer
   them to the offline signing machine.
3. In a clean directory on the offline machine, sign those exact candidate
   files:

   ```text
   python tools/sign_release_manifest.py --private-key-file X:\private\frameshift-release-key.txt --version MAJOR.MINOR.PATCH --asset Frameshift.exe --asset EliteTrader.exe --output Frameshift-update.json
   ```

4. Transfer only `Frameshift-update.json` and
   `Frameshift-update.json.sig` back to an online machine. Upload both files to
   the existing draft release using the GitHub UI or `gh release upload`.
5. Run **Publish offline-signed release** from the Actions tab and enter the
   draft tag. That manual workflow checks out the production verifier from the
   default branch, downloads all four artifacts, verifies the
   signature and both executable digests, and publishes only if every check
   succeeds.

For an additional local check before upload:

```text
python tools/verify_release_manifest.py --version MAJOR.MINOR.PATCH --manifest Frameshift-update.json --signature Frameshift-update.json.sig --asset Frameshift.exe --asset EliteTrader.exe
```

Checksums remain beside the executables for legacy clients and manual transfer
inspection. Current clients never treat a checksum delivered through the same
release channel as publisher authentication.
