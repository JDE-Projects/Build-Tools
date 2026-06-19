# Build-Tools

Shared GitHub Actions recipe for JDE-Projects "Simple X Tools".

It builds a Windows exe on a clean GitHub runner, packages it as both a
portable zip and a Windows installer, publishes SHA-256 checksums, attaches
a GitHub-signed [build provenance attestation] over both files, and waits
for maintainer approval before publishing the release. This lets anyone
confirm a downloaded binary was built from public source — no need to trust
the maintainer's laptop.

## How an app uses it

Add `.github/workflows/release.yml` to the app repo:

```yaml
name: Release
on:
  push:
    tags: ['v*']
jobs:
  release:
    uses: JDE-Projects/Build-Tools/.github/workflows/release.yml@v1
    permissions:
      id-token: write
      attestations: write
      contents: write
    with:
      app_name: YourAppName
```

Then: push a `v*` tag &rarr; it builds &rarr; approve the release in GitHub &rarr; published.

### Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `app_name` | yes | — | Used for the zip name and default dist folder |
| `python_version` | no | `3.14` | Match the app's pinned Python |
| `dist_subfolder` | no | `app_name` | PyInstaller `--onedir` output folder under `dist\` |
| `build_command` | no | `Build.bat` | The app's build script (runs in `cmd`) |
| `build_installer` | no | `true` | Also build a Windows installer (`<app_name>-<tag>-setup.exe`) via Inno Setup |
| `app_id` | when `build_installer` | — | Stable GUID used as the installer's AppId (generate one per app, never change it) |

## Verifying a download

The build is signed by the **reusable workflow in this repo**, so the verify
command must name `--signer-repo JDE-Projects/Build-Tools`. Without it, the
check fails with a misleading `issuer "sigstore.dev"` error.

```
gh attestation verify YourAppName-v1.0.0.zip \
  --repo JDE-Projects/YourAppName \
  --signer-repo JDE-Projects/Build-Tools
```

The same command works for the installer — just substitute the `-setup.exe`:

```
gh attestation verify YourAppName-v1.0.0-setup.exe \
  --repo JDE-Projects/YourAppName \
  --signer-repo JDE-Projects/Build-Tools
```

A `Verification succeeded!` line means the file was built by this pipeline from
the named public source — nothing else.

[build provenance attestation]: https://docs.github.com/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds
