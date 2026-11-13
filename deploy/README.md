# LaunchAgent template

`com.pocketbooks.plist` is a template, not a ready-to-load service. Before
installing it, replace every `REPLACE_WITH_YOUR_USERNAME` path with your macOS
username and confirm that the repository and virtual environment are at those
locations. Confirm the Homebrew FFmpeg path as well.

Then copy the edited file to `~/Library/LaunchAgents/com.pocketbooks.plist` and
load it for your user. The server binds to all interfaces by default so devices
on your trusted LAN or Tailscale network can reach it; keep the firewall enabled
and never expose the port to the public internet.
