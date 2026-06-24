# Creates launcher shortcut in the project folder AND on the Desktop
# Run this once; both shortcuts always stay in sync (same target)

$appDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue)?.Source
if (-not $pythonw) {
    Write-Host "ERROR: pythonw not found. Install Python first." -ForegroundColor Red
    pause; exit 1
}

$mainPy  = Join-Path $appDir "main.py"
$icoPath = Join-Path $appDir "icon.ico"

function New-AppShortcut($dest) {
    $ws  = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($dest)
    $lnk.TargetPath       = $pythonw
    $lnk.Arguments        = "`"$mainPy`""
    $lnk.WorkingDirectory = $appDir
    $lnk.IconLocation     = "$icoPath,0"
    $lnk.Description      = "Screen Presence Guard"
    $lnk.Save()
}

# 1) Launcher in project folder
$folderLnk = Join-Path $appDir "Screen Presence Guard.lnk"
New-AppShortcut $folderLnk
Write-Host "Folder launcher : $folderLnk" -ForegroundColor Green

# 2) Desktop shortcut
$desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Screen Presence Guard.lnk"
New-AppShortcut $desktopLnk
Write-Host "Desktop shortcut: $desktopLnk" -ForegroundColor Green

Write-Host "`nBoth point to: $pythonw `"$mainPy`"" -ForegroundColor Cyan
