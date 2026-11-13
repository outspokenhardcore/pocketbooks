#!/usr/bin/env bash
#
# Capture the App Store screenshot set from a real PocketBooks server.
#
#   PBS_URL=http://127.0.0.1:8725 PBS_TOKEN=... ./screenshots.sh
#
# Output: ios/screenshots/<device>/01-library.png …
#
# The server must be running and its library must contain the public-domain
# samples (`python -m app.samples`). The token is written straight into the
# simulator's preferences for the app and never reaches the repository.
set -euo pipefail

cd "$(dirname "$0")"

: "${PBS_URL:?Set PBS_URL to your running PocketBooks server, e.g. http://127.0.0.1:8725}"
: "${PBS_TOKEN:?Set PBS_TOKEN to the bearer token the server printed on startup}"

BUNDLE_ID="${BUNDLE_ID:-com.example.pocketbooks}"
SCHEME="PocketBooks"
PROJECT="PocketBooks.xcodeproj"
DERIVED="${DERIVED:-build/screenshots}"
OUT_ROOT="screenshots"

# App Store Connect currently asks for one 6.9" iPhone and one 13" iPad set.
DEVICES=(
  "iPhone 16 Pro Max"
  "iPad Pro 13-inch (M4)"
)

command -v xcodegen >/dev/null || { echo "xcodegen missing: brew install xcodegen"; exit 1; }

if ! curl -fsS -H "Authorization: Bearer ${PBS_TOKEN}" "${PBS_URL}/api/library" >/dev/null; then
  echo "Cannot reach ${PBS_URL} with that token. Start the server first." >&2
  exit 1
fi

echo "==> Generating the Xcode project"
xcodegen generate >/dev/null

slug() { echo "$1" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-'; }

for device in "${DEVICES[@]}"; do
  echo
  echo "==> ${device}"
  udid=$(xcrun simctl list devices available \
    | grep -F "${device} (" | head -1 | sed -E 's/.*\(([0-9A-F-]{36})\).*/\1/')
  if [ -z "${udid}" ]; then
    echo "    no such simulator, skipping" >&2
    continue
  fi

  xcrun simctl boot "${udid}" 2>/dev/null || true
  xcrun simctl bootstatus "${udid}" -b >/dev/null 2>&1 || true

  # English device language and the status bar Apple uses in its own marketing.
  xcrun simctl spawn "${udid}" defaults write -g AppleLanguages -array en-US
  xcrun simctl spawn "${udid}" defaults write -g AppleLocale -string en_US
  xcrun simctl status_bar "${udid}" override \
    --time "9:41" --dataNetwork wifi --wifiMode active --wifiBars 3 \
    --cellularMode active --cellularBars 4 \
    --batteryState charged --batteryLevel 100 >/dev/null 2>&1 || true

  echo "    building"
  xcodebuild build-for-testing -project "${PROJECT}" -scheme "${SCHEME}" \
    -destination "platform=iOS Simulator,id=${udid}" \
    -derivedDataPath "${DERIVED}" CODE_SIGNING_ALLOWED=NO >/dev/null

  app_path="${DERIVED}/Build/Products/Debug-iphonesimulator/${SCHEME}.app"
  xcrun simctl install "${udid}" "${app_path}"

  # Point the installed app at the server. Writing preferences instead of
  # launch arguments keeps the token out of the test log.
  xcrun simctl spawn "${udid}" defaults write "${BUNDLE_ID}" baseURL "${PBS_URL}"
  xcrun simctl spawn "${udid}" defaults write "${BUNDLE_ID}" token "${PBS_TOKEN}"
  xcrun simctl spawn "${udid}" defaults write "${BUNDLE_ID}" appearance "dark"

  result="${DERIVED}/${udid}.xcresult"
  rm -rf "${result}"
  echo "    driving the app"
  xcodebuild test-without-building -project "${PROJECT}" -scheme "${SCHEME}" \
    -only-testing:PocketBooksUITests/ScreenshotTests \
    -destination "platform=iOS Simulator,id=${udid}" \
    -derivedDataPath "${DERIVED}" -resultBundlePath "${result}" \
    CODE_SIGNING_ALLOWED=NO >/dev/null

  out="${OUT_ROOT}/$(slug "${device}")"
  rm -rf "${out}"; mkdir -p "${out}"
  raw="${DERIVED}/attachments-$(slug "${device}")"
  rm -rf "${raw}"
  xcrun xcresulttool export attachments --path "${result}" --output-path "${raw}" >/dev/null

  # The manifest maps each attachment back to the name the test gave it.
  python3 - "${raw}" "${out}" <<'PY'
import json, pathlib, shutil, sys
raw, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
manifest = json.loads((raw / "manifest.json").read_text())
kept = 0
for test in manifest:
    for item in test.get("attachments", []):
        name = item.get("suggestedHumanReadableName") or ""
        exported = item.get("exportedFileName") or ""
        if not name.endswith(".png"):
            continue
        # "01-library_0_<uuid>.png" -> "01-library.png"
        clean = name.split("_")[0]
        shutil.copyfile(raw / exported, out / f"{clean}.png")
        kept += 1
print(f"    {kept} screenshots -> {out}")
PY

  for png in "${out}"/*.png; do
    [ -e "${png}" ] || continue
    size=$(sips -g pixelWidth -g pixelHeight "${png}" | awk '/pixel/ {printf "%s", $2" "}')
    echo "      $(basename "${png}") ${size}"
  done
done

echo
echo "Done. Upload these in App Store Connect under App Previews and Screenshots."
