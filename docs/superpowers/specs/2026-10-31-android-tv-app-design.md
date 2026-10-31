# Android TV app + scan-to-pair — design

Date: 2026-10-31 · Status: approved by the user in chat

## Goal

A customer installs "Khanshoof" from the Play Store on their TV, scans the code
on screen with their phone, and has their content playing in seconds.

## Finding: scan-to-pair already exists

- The player (`player/`) renders a QR for `${APP_URL}/pair?code=<CODE>` next to
  the code, using the vendored `vendor/qrcode.js` (CSP `script-src 'self'`
  satisfied).
- The dashboard (`frontend/app.js`) opens `/pair?code=` prefilled, and resumes it
  after a password sign-in (`pair_resume` in sessionStorage) and after Google
  sign-in of an existing account (`return_to`).
- Backend: `POST /screens/request_code` → `GET /screens/poll/{code}` (TV, every
  3 s) and `POST /screens/claim` (dashboard). No backend change needed.

Contract stays **`https://app.khanshoof.com/pair?code=<CODE>`**.

### Dashboard gaps (owned by the dashboard redesign session)

1. Email sign-up completion and Google new-account completion (`complete-signup`
   → `location.replace("/")`) ignore `pair_resume`, so a brand-new customer
   loses the code.
2. Not one tap: arriving from a QR, preselect "new screen" with a prefilled name
   (e.g. "Screen 3") so pairing is a single **Pair** tap.

## Part 1 — player changes

- Steps lead with scanning ("Scan with your phone's camera"), typing the code
  is the fallback. EN + AR.
- Drop the wrong "Code refreshes every 10 minutes" (TTL is 5 min).
- A freshly paired screen with nothing assigned says "You're connected! Pick
  what to show from your phone." instead of "No content assigned".
- No CSP, service-worker or backend change.

## Part 2 — Android TV app (`android-tv/`)

Kotlin, one Activity hosting a WebView on `https://play.khanshoof.com`.
No TWA (Google TV often has no Chrome), no Capacitor (extra toolchain, no gain).

- Package `com.khanshoof.tv`; label "Khanshoof" / "خنشوف".
- minSdk 23, target/compile 36 (Play requires ≥34 for TV).
- Manifest: `LEANBACK_LAUNCHER` + `LAUNCHER`, 320×180 `android:banner`,
  `android.software.leanback` required, `android.hardware.touchscreen` not
  required, landscape, `INTERNET`, `ACCESS_NETWORK_STATE`,
  `RECEIVE_BOOT_COMPLETED`, `SYSTEM_ALERT_WINDOW` (boot start on Android 10+).
- WebView: JS, DOM storage (screen token lives in localStorage), autoplay
  without gesture, service workers (WebView default), HTTPS only, top-level
  navigation pinned to the player origin, UA suffix `KhanshoofTV/<version>`,
  transparent default video poster, text zoom fixed at 100 %.
- Keep screen on; immersive full screen.
- Recovery: main-frame load error or 5xx → native "Reconnecting…" overlay,
  retry 5 s → 60 s backoff, instant retry when the network returns; renderer
  gone → rebuild the WebView; hang watchdog (JS ping every 60 s, rebuild if no
  answer in 30 s).
- Back: "Press BACK again to exit" within 2 s.
- WebView older than Chrome 66 (Android 9's stock WebView, the player's
  floor) → "Update Android System WebView" screen.
- Boot: `BOOT_COMPLETED` receiver starts the Activity. Android 10+ needs
  "Display over other apps": asked once after first load (skippable); adb
  fallback documented.
- Debug builds: cleartext allowed, player URL overridable (`-PplayerUrl=` or
  `-e url` intent extra) for LAN testing.

### Build

`android-tv/Dockerfile` pins JDK 17, cmdline-tools (sha256), platform 36,
build-tools; Gradle wrapper pins the distribution sha256; AGP/Kotlin/AndroidX
pinned. `android-tv/build.sh` → `dist/` signed release `.aab` + debug `.apk`;
versionCode = git commit count. Upload keystore gitignored, created by
`make-upload-key.sh`.

## Testing

- E2E: real backend image + throwaway Postgres (own containers and network,
  seeded admin, nothing touches production); Playwright TV (1920×1080) and
  phone (390×844) contexts with the production hostnames routed to the local
  containers; the QR is decoded from the TV screenshot, not assumed.
- App: Docker build, `aapt2 dump badging`, lint. No emulator on this LXC (no
  `/dev/kvm`); the user sideloads the debug APK on a real TV.

## Deliverables

Store images (banner, icon, TV banner, feature graphic, screenshots),
`android-tv/PLAY_STORE_CHECKLIST.md` (you vs. prepared), a privacy policy
draft for the user's legal review.

## Changes made while building

- The privacy draft is `android-tv/store/privacy-policy-draft.html`, not
  `landing/privacy.html`: an unreviewed legal page must not go live with the
  next landing deploy. The checklist says where to move it once reviewed.
- The E2E test became a reusable harness, `scripts/e2e-scan-to-pair/run.sh`
  (builds player + dashboard from the working tree), so the dashboard redesign
  can re-check the `/pair?code=` contract.
- Auto-start is asked only after the TV has paired (the watchdog probe reads
  `localStorage.screen_token`), and the dialog dismisses itself after 60 s so
  an unattended TV is never stuck behind it.
- Not TV-only (user, 2026-10-31: large Android tablets are used too):
  `android.software.leanback` not required, so Play offers the app to tablets
  and phones; adaptive launcher icon; touch-safe WebView (no long-press
  selection, zoom or overscroll); copy says "screen", not "TV"; the player's
  welcome sizes for portrait; the harness checks 9:16 and 16:10 touch screens.
  Displays without Play get the Play-signed universal APK from Play Console.
- Play review pass (user, 2026-10-31: "make sure it gets approved"), against
  the official TV checklist (2026-06-29):
  - BACK exits at once on TVs (TV-DB); the double press stays for touch screens.
  - The welcome screen and native error screen animate their mascot (TV-BY),
    and the unsupported-WebView screen lets the TV sleep.
  - Videos and timers pause when leaving the app (TV-NP).
  - Launcher icons are 160 px at xhdpi (TV-LB).
  - The D-pad focus ring is a 4 px coral outline (TV-DP).
  - Auto-start is asked only where the overlay settings screen exists.
  - Robolectric launch tests run on TV and tablet configurations.
  - REVIEW_KIT.md maps every TV criterion and holds paste-ready review text.
- App data is excluded from device-to-device transfer too
  (`dataExtractionRules`): Android 12+ ignores `allowBackup` there, and a
  copied screen token would make two TVs the same screen.
