# CoMind Windows Quick Start

This guide is for Windows users who downloaded a CoMind release package.

## Which Package Should I Use?

### Normal user package

Use this if you only want to run CoMind:

```text
comind-<version>-windows-x86_64.zip
```

How to use:

1. Unzip the package.
2. Open the `CoMind` folder.
3. Double-click `Start CoMind.cmd`.
4. The browser should open `http://127.0.0.1:8789`.
5. Open model settings in the AI panel and save your DeepSeek or Kimi API key.

Useful files:

| File | Purpose |
|---|---|
| `Start CoMind.cmd` | Recommended startup entry for normal users |
| `Stop CoMind.cmd` | Stop CoMind when you are done |
| `CoMind.exe` | The app executable; direct double-click also works |
| `README-Windows-User.md` | Short user-package notes |

Normal users do not need Python, Git, Node.js, or npm.

Tip: unzip to a short path such as `C:\CoMind` or the Desktop. The package uses
short internal folder names to avoid Windows path length issues.

If Windows SmartScreen warns about an unsigned app, only continue if the package
came from a trusted CoMind release. Click "More info" and then "Run anyway".

## Developer / Deployment Package

Use this if you want source deployment, testing, service setup, or repackaging:

```text
comind-<version>-windows-x86_64-dev.zip
```

Recommended first run:

```powershell
cd comind-<version>-windows-x86_64-dev
.\install.cmd
```

Daily commands:

| Command | Purpose |
|---|---|
| `install.cmd` | First-time install or rebuild dependencies |
| `start.cmd` | Start CoMind |
| `stop.cmd` | Stop CoMind |
| `status.cmd` | Check port, process, logs, and data paths |
| `test.cmd` | Run local checks and pytest |

Optional maintainer commands:

| Command | Purpose |
|---|---|
| `service-install.cmd` | Start CoMind automatically after Windows login |
| `service-uninstall.cmd` | Remove Windows auto-start |
| `update.cmd` | Reinstall/rebuild after replacing source files |
| `package-windows.cmd` | Build user and developer zip packages |

Most developers only need:

```text
install.cmd
start.cmd
stop.cmd
test.cmd
```

## Data Locations

CoMind stores user data outside the program folder:

| Data | Location |
|---|---|
| Mind maps | `%USERPROFILE%\comind-maps` |
| API keys | `%LOCALAPPDATA%\CoMind\private\keys.json` |
| Chat sessions | `%LOCALAPPDATA%\CoMind\chat-sessions` |
| Logs | `%LOCALAPPDATA%\CoMind\comind.log` for source scripts, `%LOCALAPPDATA%\CoMind\comind-exe.log` for the click-to-run exe |

Deleting or replacing the app folder does not automatically delete your mind maps or API keys.

If the click-to-run package shows `Internal Server Error`, ask the user to run:

```powershell
Get-Content "$env:LOCALAPPDATA\CoMind\comind-exe.log" -Tail 80
```

If the AI panel reports `Cannot find module ... dist\cli.js`, the user package
is incomplete or old. Rebuild with the latest `package-windows.cmd` and send the
new `comind-<version>-windows-x86_64.zip`.

## Build Release Packages

Maintainers can build both Windows packages from the repository root:

```powershell
.\package-windows.cmd
```

The output is written to:

```text
dist-release\
```

For packaging details, see `WINDOWS_PACKAGING.md`.
