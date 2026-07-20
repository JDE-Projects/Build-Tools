# Build-Tools

Shared GitHub Actions recipe for JDE-Projects "Simple X Tools".

It builds a Windows exe on a clean GitHub runner, packages it as both a
portable zip and a Windows installer, publishes SHA-256 checksums, attaches
a GitHub-signed [build provenance attestation] over both files, and waits
for maintainer approval before publishing the release. This lets anyone
confirm a downloaded binary was built from public source, with no need to
trust the maintainer's laptop.

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

Then: push a `v*` tag &rarr; it builds &rarr; approve the release in GitHub &rarr; published &rarr; replace the auto-generated notes with a concise `## What's Changed` (`gh release edit`).

### Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `app_name` | yes | (none) | Used for the zip name and default dist folder |
| `python_version` | no | `3.14` | Match the app's pinned Python |
| `dist_subfolder` | no | `app_name` | PyInstaller `--onedir` output folder under `dist\` |
| `build_command` | no | `Build.bat` | The app's build script (runs in `cmd`) |
| `build_installer` | no | `true` | Also build a Windows installer (`<app_name>-<tag>-setup.exe`) via Inno Setup |
| `app_id` | when `build_installer` | (none) | Stable GUID used as the installer's AppId (generate one per app, never change it) |
| `entry_script` | no | (none) | Tool's main `.py` filename (e.g. `simple_ssh_tool.py`); when set, CI imports the module before building to catch syntax/import/dependency breakage |

Before the build step, the workflow also runs three CI sanity checks, in order:
it lints the whole repo with `ruff` using an explicit rule set
(`--select E4,E7,E9,F,B`, not ruff's defaults), it imports `entry_script`
(if set), and if the repo has a `tests/` folder, it runs that folder with
`pytest`. The ruff and pytest versions come from `templates/dev/requirements.txt`,
which the workflows install and which Dependabot keeps current; tool repos copy
it as `requirements-dev.txt` so local checks match CI.

## Verifying a download

The build is signed by the **reusable workflow in this repo**, so the verify
command must name `--signer-repo JDE-Projects/Build-Tools`. Without it, the
check fails with a misleading `issuer "sigstore.dev"` error.

```
gh attestation verify YourAppName-v1.0.0.zip \
  --repo JDE-Projects/YourAppName \
  --signer-repo JDE-Projects/Build-Tools
```

The same command works for the installer, just substitute the `-setup.exe`:

```
gh attestation verify YourAppName-v1.0.0-setup.exe \
  --repo JDE-Projects/YourAppName \
  --signer-repo JDE-Projects/Build-Tools
```

A `Verification succeeded!` line means the file was built by this pipeline from
the named public source, nothing else.

[build provenance attestation]: https://docs.github.com/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds
