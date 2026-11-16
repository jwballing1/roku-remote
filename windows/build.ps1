# Builds the Windows app (windows\dist\TV Remote.exe) from app.py here and the server and page in ..\web.
# Run from PowerShell in this folder:  .\build.ps1
# The first run sets up a private Python environment in .venv, which takes a few minutes.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Web = Join-Path (Split-Path $Root -Parent) "web"
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Setting up the build environment in .venv..."
    python -m venv $Venv
}
# certifi is the standard list of trusted certificate authorities. Bundling it means the app can reach the
# family hub even on PCs whose own Windows list is missing Let's Encrypt's root.
& $Python -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ('webview', 'PyInstaller', 'certifi')) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install --quiet --disable-pip-version-check pywebview pyinstaller certifi
    if ($LASTEXITCODE -ne 0) { throw "Installing pywebview, pyinstaller and certifi failed." }
}

Write-Host "Building TV Remote.exe..."
& $Python -m PyInstaller --noconfirm --onefile --windowed --name "TV Remote" `
    --icon (Join-Path $Root "icon.ico") `
    --add-data "$(Join-Path $Web 'index.html');." `
    --paths $Web `
    --hidden-import certifi `
    --collect-data certifi `
    --distpath (Join-Path $Root "dist") `
    --workpath (Join-Path $Root "build") `
    --specpath (Join-Path $Root "build") `
    (Join-Path $Root "app.py")
if ($LASTEXITCODE -ne 0) { throw "The build failed." }
Write-Host "Done: $(Join-Path $Root 'dist\TV Remote.exe')"
