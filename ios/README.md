# iOS client

The SwiftUI client connects to a PocketBooks backend on your trusted home
network or Tailscale tailnet. It streams generated audio with AVPlayer and can
download completed M4B files for offline listening.

## Generate and build

The Xcode project is generated from `project.yml` and is not checked into Git.
Install XcodeGen, then run:

```bash
brew install xcodegen
cd ios
xcodegen generate
open PocketBooks.xcodeproj
```

Choose your own Apple Developer signing team in Xcode. Do not reuse another
developer's team identifier. Build and run on an iPhone reachable from the
server. Before installing on a physical device, replace the example
`PRODUCT_BUNDLE_IDENTIFIER` in `project.yml` with a unique identifier that
belongs to your Apple Developer account, then run `xcodegen generate` again.

## First launch

In the app settings, enter:

- **Server URL:** for example `http://<mac-lan-ip>:8725` on a trusted LAN, or
  your Tailscale URL.
- **API token:** printed by the backend when it starts.

## Current scope

In the demo catalogue, returning to the app or pulling down in the library
fetches the current book list from the host.

The client includes library browsing, conversion progress, HLS playback,
chapter seeking, listening-position sync, and completed M4B downloads. Offline
HLS downloads and a separate downloads-management screen are not implemented;
the completed M4B download is the offline path.

See the root [README](../README.md) for architecture, supported setup, and
project validation status. For TestFlight and App Store distribution, see
[`docs/APP_STORE.md`](../docs/APP_STORE.md).
