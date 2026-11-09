# Builds the TV Remote family hub as one image file, ready to import in the NAS's Docker app.
# No settings go in the image: you enter them on the hub's setup page after creating the container.
# Needs Docker Desktop on this PC. Run it from PowerShell:
#   powershell -ExecutionPolicy Bypass -File C:\repos\roku-remote\hub\build-image.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$output = Join-Path $repo "tvremote-hub.tar"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker isn't installed on this PC. Install Docker Desktop, start it, then run this again."
}

Write-Host ""
$arch = (Read-Host "NAS processor: 1 = Intel (most UGREEN NASync models), 2 = ARM [1]").Trim()
$platform = if ($arch -eq "2") { "linux/arm64" } else { "linux/amd64" }

Write-Host "Building the image. The first time takes a few minutes..." -ForegroundColor Cyan
docker build --platform $platform -f (Join-Path $PSScriptRoot "Dockerfile") -t tvremote-hub $repo
if ($LASTEXITCODE -ne 0) { throw "The build failed. See the messages above." }
docker save tvremote-hub -o $output
if ($LASTEXITCODE -ne 0) { throw "Saving the image file failed. See the messages above." }

Write-Host ""
Write-Host "Done: $output" -ForegroundColor Green
Write-Host ""
Write-Host "On the NAS, in the Docker app:"
Write-Host "  1. Images: import tvremote-hub.tar."
Write-Host "  2. Create a container from the tvremote-hub image:"
Write-Host "       Ports:   8443 -> 8443, 8088 -> 80, 8080 -> 8080  (UGREEN uses port 80 itself)"
Write-Host "       Storage: a NAS folder, like docker/tvremote-hub -> /data"
Write-Host "       Restart: always"
Write-Host "  3. On your home network, open http://<NAS address>:8080 and fill in the settings."
