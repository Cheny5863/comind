# Windows Packaging

CoMind ships two Windows-friendly package types.

For package usage and script selection, read `WINDOWS_QUICKSTART.md` first.

## 1. User package

Target: non-technical users.

Output:

- `dist-release/CoMind-user-<version>-win-x64.zip`

The zip contains a short top-level `CoMind` folder. Runtime folders inside the
user package intentionally use short names (`n` for portable Node.js and `p`
for the pi runtime). The pi agent itself is moved to `p/a` after installation
to avoid Windows extraction failures caused by long `node_modules` paths.

Contents:

- `CoMind.exe`
- portable Node.js runtime
- packaged `pi-coding-agent`
- `Start CoMind.cmd`
- `Stop CoMind.cmd`
- `README-Windows-User.md`

The user only needs to unzip and double-click `Start CoMind.cmd`.

## 2. Developer package

Target: Windows developers and maintainers.

Output:

- `dist-release/CoMind-windows-dev-<version>.zip`

Contents:

- source code
- Windows install/start/stop/status/test/service scripts
- built frontend assets when present
- no `.git`, `.venv`, `node_modules`, or release build cache

The developer can run:

```powershell
cd comind
.\install.cmd
.\test.cmd -AI
.\service-install.cmd
```

## Build Command

Run from the repository root on Windows:

```powershell
.\package-windows.cmd
```

Useful options:

```powershell
.\package-windows.cmd -SkipWebBuild
.\package-windows.cmd -NodeZip C:\path\to\node-v22.19.0-win-x64.zip
.\package-windows.cmd -SkipDevZip
```

`-NodeZip` is useful in China or CI environments where direct downloads from
`nodejs.org` are slow or blocked.

## Prerequisites

- Windows x64
- Python 3.12+
- Node.js/npm for building
- Network access to PyPI, npm, and Node.js download servers, unless dependencies
  are already cached and `-NodeZip` is supplied

The script installs Python build dependencies, runs the frontend build, builds
`CoMind.exe` with PyInstaller, downloads portable Node.js, installs
`@earendil-works/pi-coding-agent` into the user package, and creates zip files.

Before declaring the user package ready, the script verifies that the zip
contains the required runtime files:

- `CoMind/CoMind.exe`
- `CoMind/_internal/index.html`
- `CoMind/_internal/map-switcher.js`
- `CoMind/p/a/dist/cli.js`

If any of these files are missing, the build fails instead of producing a
package that opens but cannot load the editor or AI agent.
The script also fails if any zip entry path becomes too long for beginner-friendly
Windows extraction.

## Runtime Data

Both package types store user data outside the app directory:

- maps: `%USERPROFILE%\comind-maps`
- keys and sessions: `%LOCALAPPDATA%\CoMind`

This keeps upgrades and reinstallations from deleting user content.
