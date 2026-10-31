# Khanshoof for Android TV and tablets

The Play Store app that turns an Android TV / Google TV, or a large Android
tablet or touch display, into a Khanshoof screen. It is a small Kotlin app around one full-screen WebView that runs the
player from https://play.khanshoof.com; all the signage logic stays in
`player/`, so player updates reach app TVs without an app update.

What the app adds on top of opening the player in a browser:

| | |
|---|---|
| Home screen | TV launcher entry with a 320×180 banner; a normal app icon (adaptive, so tablet launchers shape it) for tablets and non-Google TV boxes |
| Kiosk | full screen, screen kept on, landscape or portrait, D-pad works, no touchscreen needed |
| Touch-safe | on tablets a long press selects nothing and pinch doesn't zoom |
| Starts by itself | after the TV boots (Android 10+: once "Display over other apps" is allowed, see below) |
| Recovers | retries with backoff when the player can't load, retries at once when the network returns, replaces the WebView if its renderer crashes or hangs |
| Offline | service workers on, so the player's media cache keeps playing without internet |
| Safe exit | BACK twice to leave, so a bumped remote doesn't take the menu off the wall |
| Old TVs | asks for an Android System WebView update if it is older than Chrome 80 |

Package: `com.khanshoof.tv` (debug builds: `com.khanshoof.tv.debug`, installs alongside).
Android 6.0+ (minSdk 23), targets Android 16 (API 36). Not TV-only: Play offers
it to TVs, tablets and phones (`android.software.leanback` is not required).

## Build

Needs only Docker. The toolchain (JDK 17, Android SDK 36, Gradle 8.14.5) is
pinned in the [Dockerfile](Dockerfile); plugins and libraries in the Gradle files.

```bash
android-tv/build.sh --debug-only
```

Unit tests and lint, then `dist/khanshoof-tv-<version>-<code>-debug.apk`.

```bash
android-tv/make-upload-key.sh
```

Once, before the first Play upload: creates `keystore/` (gitignored). Back it up.

```bash
VERSION_NAME=1.0.0 android-tv/build.sh
```

Also `dist/khanshoof-tv-<version>-<code>-release.aab`, signed with the upload key.
The version code is the git commit count unless `VERSION_CODE` is set; Play
needs it to grow with every upload. The script refuses to finish if the bundle
isn't signed.

Two clean builds of the same commit give byte-identical files: the debug APK,
and the release bundle too when signed with the same upload key (checked by
building twice from scratch and comparing SHA-256 sums).

The first build downloads Gradle plugins and libraries into `.cache/`; later
builds are offline and take about a minute.

## Try it on a TV or tablet

On a tablet: Settings → About tablet → tap **Build number** 7 times, then
Developer options → **USB debugging** on, and use `adb install` as below. Or copy
the APK onto it and open it (allow "Install unknown apps" for the file manager).

On a TV:

1. On the TV: Settings → Device Preferences → About → click **Build** 7 times
   to unlock developer options, then Developer options → **USB / network
   debugging** on. Note the TV's IP address (Settings → Network).
2. From this machine:

   ```bash
   adb connect 192.168.1.50:5555
   ```

   Accept the prompt on the TV, then:

   ```bash
   adb install -r android-tv/dist/khanshoof-tv-1.0.0-*-debug.apk
   ```

   No adb? `docker run --rm -it --network host -v "$PWD":/w -w /w khanshoof-tv-build adb ...`
   works too (`platform-tools` is in the build image).
3. Open **Khanshoof (debug)** from the TV's apps row, scan the code with your phone.

Without adb: put the APK on a USB stick and open it with a file manager on
the TV, or use the "Downloader" / "Send Files to TV" apps.

### Against a local stack

A debug build can open a player on your LAN instead of production:

```bash
PLAYER_URL=http://192.168.1.20:3001/ android-tv/build.sh --debug-only
```

or, without rebuilding:

```bash
adb shell am start -n com.khanshoof.tv.debug/com.khanshoof.tv.MainActivity -e url http://192.168.1.20:3001/
```

Release builds always open `https://play.khanshoof.com/` over HTTPS only.

### Useful adb commands

```bash
adb logcat -s KhanshoofTV                     # the app's own log lines
adb shell appops set com.khanshoof.tv SYSTEM_ALERT_WINDOW allow   # allow start-on-boot without the settings screen
adb shell am broadcast -a android.intent.action.BOOT_COMPLETED -p com.khanshoof.tv   # pretend the TV just booted
```

Debug builds also allow `chrome://inspect` remote debugging of the player.

## Tablets and Android displays without the Play Store

Many large-format Android displays ship without Google Play. For those:

- **Install** the Play-signed universal APK: Play Console → App bundle
  explorer → (the release) → Downloads → *Signed, universal APK*. It is signed
  with the same key as Play installs, so a display can move to Play later
  without reinstalling and re-pairing. Don't hand out an APK signed with the
  upload key: it would clash with Play installs.
- **Updates** need no app reinstall for player changes: the app loads the
  player from play.khanshoof.com every time.
- **WebView age** is the real limit. The player needs Chrome 80 or newer
  (Settings → Apps → Android System WebView shows the version). Without Play
  the WebView only updates with the maker's system updates, so in practice
  that means roughly Android 11 or newer, or a maker who updated it. Older
  panels get a "This screen needs an update" message instead of a broken page.
- **Public tablets**: BACK twice stops accidental exits, but anyone can still
  swipe home. Turn on *App pinning* (Settings → Security → App pinning) to keep
  passers-by in the app.

## Start on boot

Android 9 and older start the app after boot with no setup. Android 10+ only
allow it for apps the user has let "Display over other apps" (the app never
draws over other apps). After the TV is paired the app asks once, with a
dialog that disappears by itself after a minute if nobody answers (it asks
again next launch). Some TVs have no settings screen for this; use the
`appops` command above.

Many TVs don't reboot but sleep; when they wake they may show the home screen
instead of the last app. That's the TV's choice, not something an app can
override.

## How it fits together

| File | Job |
|---|---|
| [`MainActivity.kt`](app/src/main/java/com/khanshoof/tv/MainActivity.kt) | lifecycle, full screen, retry/backoff, network watch, hang watchdog, BACK |
| [`PlayerWebView.kt`](app/src/main/java/com/khanshoof/tv/PlayerWebView.kt) | the WebView settings and its clients (errors, renderer gone, origin pinning) |
| [`Policy.kt`](app/src/main/java/com/khanshoof/tv/Policy.kt) | backoff steps, origin rules, WebView version check (unit-tested) |
| [`AutoStart.kt`](app/src/main/java/com/khanshoof/tv/AutoStart.kt), [`BootReceiver.kt`](app/src/main/java/com/khanshoof/tv/BootReceiver.kt) | start on boot |
| [`store/render_assets.py`](store/render_assets.py) | renders the icons, banner and Play graphics from the mascot art |

The watchdog's probe reads `localStorage.screen_token` (the key
`player/player.js` uses) to know when the TV has paired. Rename that key in
the player and the auto-start question simply never appears.

Publishing: see [PLAY_STORE_CHECKLIST.md](PLAY_STORE_CHECKLIST.md).
