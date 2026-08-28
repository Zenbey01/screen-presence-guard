$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appDir

# Clean old builds
Remove-Item -Recurse -Force dist, build, ScreenPresenceGuard.spec -ErrorAction SilentlyContinue

pyinstaller `
  --name "ScreenPresenceGuard" `
  --windowed `
  --icon "icon.ico" `
  --add-data "icon.ico;." `
  --add-data "blaze_face_short_range.tflite;." `
  --collect-all mediapipe `
  --collect-all customtkinter `
  --collect-all cv2 `
  --hidden-import pystray `
  --hidden-import PIL._tkinter_finder `
  "main.py"

if (Test-Path "dist\ScreenPresenceGuard") {
    $bundle = "dist\ScreenPresenceGuard"
    $required = @(
        "$bundle\ScreenPresenceGuard.exe",
        "$bundle\_internal\icon.ico",
        "$bundle\_internal\blaze_face_short_range.tflite"
    )
    $missing = $required | Where-Object { -not (Test-Path $_) }
    if ($missing) {
        Write-Host "Build FAILED — missing bundle files:" -ForegroundColor Red
        $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        exit 1
    }
    Write-Host "`nBuild OK! — dist\ScreenPresenceGuard\" -ForegroundColor Green

    # Create installer bat
    $bat = @'
@echo off
chcp 65001 >nul
set "DIR=%~dp0"
powershell -Command "$ws=New-Object -ComObject WScript.Shell;$lnk=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Screen Presence Guard.lnk');$lnk.TargetPath='%DIR%ScreenPresenceGuard.exe';$lnk.WorkingDirectory='%DIR%';$lnk.IconLocation='%DIR%ScreenPresenceGuard.exe,0';$lnk.Save()"
echo สร้าง Shortcut บน Desktop แล้ว!
pause
'@
    [System.IO.File]::WriteAllText("$appDir\dist\ScreenPresenceGuard\ติดตั้ง shortcut.bat", $bat, [System.Text.Encoding]::UTF8)

    # Zip it
    $zip = Join-Path $appDir "ScreenPresenceGuard.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path "dist\ScreenPresenceGuard\*" -DestinationPath $zip
    $sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "Zipped: $zip ($sizeMB MB)" -ForegroundColor Cyan
} else {
    Write-Host "Build FAILED" -ForegroundColor Red
}
