#!/usr/bin/env bash
# macOS counterpart of build.ps1 — builds ScreenPresenceGuard.app and zips it.
#
# Run from the project root:  ./build.sh
#
# Three things here are macOS-specific and none of them are optional:
#
#  1. --add-data uses ':' as the separator, not the ';' Windows wants.
#  2. Info.plist MUST carry NSCameraUsageDescription. Without it macOS denies
#     the camera *silently* — cv2 just reports no device, which looks exactly
#     like the MSMF bug this project already spent a release chasing.
#  3. The .app is a directory, so it is packed with ditto, not zip: plain zip
#     drops the symlinks inside Contents/Frameworks and the bundle will not
#     launch on the other side.
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/ScreenPresenceGuard.app"
ZIP="ScreenPresenceGuard-macOS.zip"

rm -rf dist build ScreenPresenceGuard.spec "$ZIP"

pyinstaller \
  --name "ScreenPresenceGuard" \
  --windowed \
  --icon "icon.icns" \
  --osx-bundle-identifier "com.zenbey.screenpresenceguard" \
  --add-data "icon.ico:." \
  --add-data "blaze_face_short_range.tflite:." \
  --collect-all mediapipe \
  --collect-all customtkinter \
  --collect-all cv2 \
  --hidden-import pystray \
  --hidden-import spgplatform._mac \
  --hidden-import PIL._tkinter_finder \
  "main.py"

[ -d "$APP" ] || { echo "Build FAILED — no $APP"; exit 1; }

PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "Screen Presence Guard" "$PLIST"
plutil -replace CFBundleName        -string "Screen Presence Guard" "$PLIST" 2>/dev/null || \
  plutil -insert  CFBundleName        -string "Screen Presence Guard" "$PLIST"
plutil -replace NSCameraUsageDescription \
  -string "ใช้กล้องเพื่อตรวจว่ามีคนอยู่หน้าจอ — ภาพไม่ถูกส่งออกจากเครื่อง" "$PLIST" 2>/dev/null || \
  plutil -insert NSCameraUsageDescription \
  -string "ใช้กล้องเพื่อตรวจว่ามีคนอยู่หน้าจอ — ภาพไม่ถูกส่งออกจากเครื่อง" "$PLIST"
plutil -replace NSHighResolutionCapable -bool true "$PLIST" 2>/dev/null || \
  plutil -insert NSHighResolutionCapable -bool true "$PLIST"

# Same guarantee build.ps1 gives: the bundle can never ship without the face
# model or the icon. _DIR resolves to Contents/Frameworks when frozen.
missing=()
for f in "$APP/Contents/MacOS/ScreenPresenceGuard" \
         "$APP/Contents/Resources/icon.icns" \
         "$APP/Contents/Frameworks/blaze_face_short_range.tflite"; do
  [ -e "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -ne 0 ]; then
  echo "Build FAILED — missing bundle files:"
  printf '  %s\n' "${missing[@]}"
  exit 1
fi

# Ad-hoc signature. Not a Developer ID, so Gatekeeper still asks the user to
# right-click -> Open the first time, and the signature changes on every build,
# which makes macOS re-ask for camera/accessibility after each new download.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" && echo "codesign: ad-hoc OK"

ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
echo "Build OK — $APP"
echo "Zipped: $ZIP ($(du -m "$ZIP" | cut -f1) MB)"
