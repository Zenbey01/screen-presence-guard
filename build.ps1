$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appDir

# Clean old builds
Remove-Item -Recurse -Force dist, build, ScreenPresenceGuard.spec -ErrorAction SilentlyContinue

pyinstaller `
  --name "ScreenPresenceGuard" `
  --windowed `
  --icon "icon.ico" `
  --add-data "icon.ico;." `
  --collect-all mediapipe `
  --collect-all customtkinter `
  --collect-all cv2 `
  --hidden-import pystray `
  --hidden-import PIL._tkinter_finder `
  "main.py"

if (Test-Path "dist\ScreenPresenceGuard") {
    Write-Host "`nBuild OK! — dist\ScreenPresenceGuard\" -ForegroundColor Green

    # Zip it
    $zip = Join-Path $appDir "ScreenPresenceGuard.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path "dist\ScreenPresenceGuard\*" -DestinationPath $zip
    $sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "Zipped: $zip ($sizeMB MB)" -ForegroundColor Cyan
} else {
    Write-Host "Build FAILED" -ForegroundColor Red
}
