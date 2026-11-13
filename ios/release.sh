#!/usr/bin/env bash
#
# Build a signed App Store archive of PocketBooks, export it, and optionally
# upload it to App Store Connect (TestFlight).
#
#   source ~/.config/pocketbooks/ios.env
#   ios/release.sh              # archive + export, stops before uploading
#   ios/release.sh --upload     # ... and upload through your Xcode account
#
# The bundle identifier and team come from the environment so this public
# repository never carries them; a fork sets its own:
#
#   export KB_IOS_BUNDLE_ID=com.yourname.pocketbooks
#   export KB_IOS_TEAM_ID=YOURTEAMID
#
# Signing is automatic: Xcode must be signed in to the Apple Developer account
# that owns the team, and it creates the App Store provisioning profile.
set -euo pipefail

cd "$(dirname "$0")"

# Xcode copies the IPA payload with rsync and expects Apple's own build. A
# Homebrew rsync earlier on PATH makes the export fail with "Copy failed".
export PATH="/usr/bin:$PATH"

: "${KB_IOS_BUNDLE_ID:?Set KB_IOS_BUNDLE_ID to a bundle identifier your team owns}"
: "${KB_IOS_TEAM_ID:?Set KB_IOS_TEAM_ID to your Apple Developer team id}"

upload=0
[ "${1:-}" = "--upload" ] && upload=1

# Build numbers must rise with every upload. Date-based, two integers
# ("261113.0725"), which App Store Connect accepts and orders numerically.
BUILD="${KB_IOS_BUILD:-$(date +%y%m%d.%H%M)}"
OUT="build/release"
ARCHIVE="${OUT}/PocketBooks.xcarchive"
EXPORT="${OUT}/export"

command -v xcodegen >/dev/null || { echo "xcodegen missing: brew install xcodegen" >&2; exit 1; }

echo "==> Bundle ${KB_IOS_BUNDLE_ID}, team ${KB_IOS_TEAM_ID}, build ${BUILD}"
xcodegen generate >/dev/null

rm -rf "${OUT}"; mkdir -p "${OUT}"

echo "==> Archiving"
xcodebuild archive \
  -project PocketBooks.xcodeproj -scheme PocketBooks -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "${ARCHIVE}" \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM="${KB_IOS_TEAM_ID}" \
  PRODUCT_BUNDLE_IDENTIFIER="${KB_IOS_BUNDLE_ID}" \
  CURRENT_PROJECT_VERSION="${BUILD}" \
  | tail -n 3

[ -d "${ARCHIVE}" ] || { echo "Archive failed" >&2; exit 1; }

# The archive must carry the identity we asked for, not a placeholder.
plist="${ARCHIVE}/Products/Applications/PocketBooks.app/Info.plist"
packed_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${plist}")
packed_build=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "${plist}")
[ "${packed_id}" = "${KB_IOS_BUNDLE_ID}" ] || { echo "bundle id is ${packed_id}" >&2; exit 1; }
echo "    archive holds ${packed_id} (${packed_build})"

if [ "${upload}" -eq 1 ]; then destination="upload"; else destination="export"; fi
cat > "${OUT}/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>teamID</key><string>${KB_IOS_TEAM_ID}</string>
  <key>signingStyle</key><string>automatic</string>
  <key>destination</key><string>${destination}</string>
  <key>uploadSymbols</key><true/>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict></plist>
PLIST

echo "==> Exporting (${destination})"
xcodebuild -exportArchive -archivePath "${ARCHIVE}" \
  -exportOptionsPlist "${OUT}/ExportOptions.plist" -exportPath "${EXPORT}" \
  -allowProvisioningUpdates | tail -n 4

if [ "${upload}" -eq 1 ]; then
  echo "Uploaded. Processing in App Store Connect takes a few minutes."
else
  ls "${EXPORT}"
  echo "Exported. Re-run with --upload once the App Store Connect record exists."
fi
