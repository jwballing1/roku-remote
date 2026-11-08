# TV Remote

A simple remote for Roku devices, with programmable channel buttons. It runs as a website you
can open from any device on the same network, or as a Windows app.

**New to TV Remote?** See the [user guide](docs/USER-GUIDE.md), with screenshots of every feature.

## Folders
| Folder | What's in it |
|---|---|
| `web\` | The website: `server.py` (the server) and `index.html` (the remote page). The Windows app uses these too. |
| `windows\` | The Windows app: `app.py` (opens the page in its own window), `build.ps1` and `icon.ico` |

## Run the website
Needs Python 3.10 or newer, and nothing else to install.

```
cd web
python server.py
```

Then open http://localhost:8000. The server prints addresses for phones and tablets on the same Wi-Fi.
Use `--port` to change the port, or `--host 127.0.0.1` to allow only this computer.

## Build the Windows app
```
cd windows
.\build.ps1
```

This creates `windows\dist\TV Remote.exe`, using the server and page from `web\`. The first build sets
up a `windows\.venv` folder with pywebview and PyInstaller.

## Saved data
The website keeps these in `web\`, next to `server.py`. The Windows app keeps its own copies in
`%APPDATA%\TV Remote`.

| File | Contents |
|---|---|
| `tv.json` | The TV currently selected |
| `channels.json` | Channel and shortcut buttons for each TV, with the app versions they last worked with |
| `settings.json` | Known TVs, view settings, schedules, the lock PIN's hash and email alert settings, including the email password in plain text |

Keep `settings.json` private, and out of any shared or public repository.
