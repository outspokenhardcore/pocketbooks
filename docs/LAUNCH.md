# Launch runbook: PocketBooks in the App Store

Everything that can be automated is. What is left needs the Apple Developer
account holder, because Apple does not expose it through any API. The whole
list takes about 20 minutes.

**State of play (13 November 2026)**

| | |
|---|---|
| Release build | Archives and signs. Bundle `de.outspoken.pocketbooks`, team id from `KB_IOS_TEAM_ID`. |
| App ID and App Store profile | Created automatically by Xcode in the developer account. |
| Listing, screenshots, privacy and support pages | Ready, tested, live. |
| Demo library for reviewers | Live: five books, 30 hours, no server needed. |
| App record in App Store Connect | Created. |
| First build | **Uploaded to TestFlight** (13 November 2026). |
| App Store Connect API key | Not on this Mac. |

## 1 · Create the app record (5 min, web)

[appstoreconnect.apple.com](https://appstoreconnect.apple.com) → *Apps* → **+** → *New App*.

| Field | Value |
|---|---|
| Platforms | iOS |
| Name | `PocketBooks` (taken? use `PocketBooks Audiobooks`) |
| Primary language | English (U.S.) |
| Bundle ID | `de.outspoken.pocketbooks` (already in the list) |
| SKU | `pocketbooks-ios` |
| User access | Full access |

## 2 · Create an API key (3 min, web)

*Users and Access* → *Integrations* → *App Store Connect API* → **Team Keys** →
generate a key with the **App Manager** role. Download the `.p8` once, note the
*Key ID* and the *Issuer ID*, and put them where the scripts read them:

```bash
mkdir -p ~/.config/pocketbooks && chmod 700 ~/.config/pocketbooks
cat > ~/.config/pocketbooks/asc.env <<'EOF'
export APP_STORE_CONNECT_KEY_ID=XXXXXXXXXX
export APP_STORE_CONNECT_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export APP_STORE_CONNECT_KEY_PATH=$HOME/.config/pocketbooks/AuthKey_XXXXXXXXXX.p8
EOF
chmod 600 ~/.config/pocketbooks/asc.env
```

The org already keeps a shared key in Infisical (`shared-store`). Reusing it is
fine; copy its three values instead of creating a new key.

## 3 · Add your phone number (1 min)

Apple wants a number the reviewer can call. Edit `KB_REVIEW_PHONE` in
`~/.config/pocketbooks/ios.env`.

## 4 · Ship the build to TestFlight (one command)

```bash
source ~/.config/pocketbooks/ios.env
ios/release.sh --upload
```

The upload signs with a cloud-managed distribution certificate, so macOS does
not ask for your keychain. The build appears in TestFlight after a few minutes
of processing. Add yourself under *TestFlight → Internal Testing*, install it
with the TestFlight app, and listen for five minutes with the screen locked,
which is the one thing the automated tests cannot check.

> **If the export fails with "Copy failed":** a Homebrew `rsync` earlier on
> `PATH` is the cause (Xcode needs Apple's own). `release.sh` already puts
> `/usr/bin` first; run `xcodebuild` by hand with the same `PATH` if you
> bypass the script.

## 5 · Upload the listing (one command)

```bash
cd ios
source ~/.config/pocketbooks/ios.env && source ~/.config/pocketbooks/asc.env
fastlane ios metadata
```

This sends the description, keywords, URLs, category, review notes, review
contact and the ten screenshots (6.9" iPhone, 13" iPad). The text lives in
[`ios/fastlane/metadata/`](../ios/fastlane/metadata) and is covered by
[`test_store_metadata.py`](../backend/tests/test_store_metadata.py).

## 6 · Answer the questions only the web form asks (5 min)

| Where | Answer |
|---|---|
| *App Privacy* | **Data Not Collected** for every category. See the [privacy page](https://www.mauli.app/pocketbooks/privacy.html). |
| *Pricing and Availability* | Free, all countries. |
| *Age Rating* | None for everything except *Violence*: **Infrequent/Mild**, because the demo books include a war novel and spy thrillers. The app itself has no web browsing or user content. |
| *Content Rights* | The app **does** access third-party content. You **do** have the rights: public domain. |
| *Export Compliance* | Already answered by `ITSAppUsesNonExemptEncryption = false`. |
| *Advertising Identifier* | No. |

## 7 · Submit

```bash
fastlane ios submit      # attaches the latest build and sends it to App Review
```

Or press *Add for Review* in the web UI. Release is **manual**, so the app does
not go live the moment Apple approves it.

## If review comes back with questions

The three the notes already answer, in the order they usually appear:

1. *No server, so nothing to test.* The demo library needs none. See the notes.
2. *Why does ATS allow arbitrary loads?* Users reach their own server over plain
   HTTP on a LAN. Explained in the notes.
3. *Is the content yours?* Public domain, documented per title in
   [`samples/README.md`](../samples/README.md).

## Listing and submission from CI

The listing and the submission can also run in GitHub Actions, so the App Store
Connect key never has to leave Infisical. The workflow is
[`store-listing.yml`](../.github/workflows/store-listing.yml). It joins the
Tailnet with GitHub's OIDC token, reads the key from the `shared-store` project
in Infisical, and runs the same fastlane lanes as your Mac.

```bash
gh workflow run store-listing.yml -f action=check      # read-only: key and app OK?
gh workflow run store-listing.yml -f action=metadata   # listing, notes, screenshots
gh workflow run store-listing.yml -f action=submit     # App Review, main branch only
```

**One-time setup**

| What | Where | Value |
|---|---|---|
| `APP_STORE_CONNECT_KEY_P8` | Infisical → `shared-store` → env `prod` | The shared key. Usually there already. |
| `APP_STORE_CONNECT_KEY_ID` | Repository **variable** | Key ID of that key. Shown permanently in App Store Connect under *Users and Access → Integrations → Team Keys*. |
| `APP_STORE_CONNECT_ISSUER_ID` | Repository **variable** | Issuer ID from the same page. |
| `KB_REVIEW_PHONE` | Repository **secret** | A number the App Review team can call. |

Already set: `IOS_BUNDLE_ID`, `APPLE_TEAM_ID`, the review contact name and
email, `KB_COPYRIGHT`, and the Infisical and Tailscale variables.

```bash
gh variable set APP_STORE_CONNECT_KEY_ID -R outspokenhardcore/pocketbooks --body '<Key ID>'
gh variable set APP_STORE_CONNECT_ISSUER_ID -R outspokenhardcore/pocketbooks --body '<Issuer ID>'
gh secret set KB_REVIEW_PHONE -R outspokenhardcore/pocketbooks     # prompts, nothing is echoed
```

The job runs in the `production` environment because the trust rules match on
it. A failed run says which of these is missing.

**If the Tailscale step fails with 403:** GitHub gives repositories created after
15 July 2026 an immutable OIDC subject with numeric IDs, for this repository
`repo:outspokende@272713119/pocketbooks@1322883625:environment:production`.
A trust rule written for the old name-based form
(`repo:outspokende/<name>:…`) rejects it. Run the `OIDC claims (debug)` workflow
to see the exact claims, then set the Tailscale trust credential and the
Infisical identity to the glob `repo:outspokende*/*:environment:production`
(plus the claim `repository_owner_id = 272713119`), which matches both forms.

The tag-driven TestFlight workflow in draft pull request #1 is a separate,
optional step for later: it needs a distribution certificate and a provisioning
profile in Infisical as well. The first build was uploaded from a Mac.
