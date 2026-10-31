# Play Store submission checklist — Khanshoof for Android TV and tablets

The app runs on Android TV / Google TV and on Android tablets and large
displays, so Play offers it to TVs, tablets and phones. The Android TV part
needs its own opt-in (step 6).

Two lists: **what's ready** (made in this repo) and **what only you can do**
(accounts, money, legal, keys). Work through "You" top to bottom; most steps
take minutes, but steps 1 and 9 involve waiting on Google.

---

## What's ready

| Item | Where | Notes |
|---|---|---|
| App source | `android-tv/app/` | Kotlin WebView kiosk around play.khanshoof.com, see [README](README.md) |
| Reproducible build | `android-tv/build.sh`, `Dockerfile` | signed `.aab` for Play + debug `.apk`; tests and lint run first |
| Upload-key script | `android-tv/make-upload-key.sh` | you run it, so the key never leaves your hands |
| TV launcher banner 320×180 | `app/src/main/res/drawable-xhdpi/banner.png` | in the app; English + Arabic name |
| Launcher icons | `app/src/main/res/mipmap-*` | adaptive icon for tablet launchers, PNGs for old ones |
| Play app icon 512×512 | `store/play-icon-512.png` | |
| Feature graphic 1024×500 | `store/play-feature-graphic-1024x500.jpg` | no transparency, as Play requires |
| TV banner 1280×720 | `store/play-tv-banner-1280x720.jpg` | the listing's TV banner (different from the in-app one) |
| TV screenshots 1920×1080 | `store/screenshots/tv-*` | pairing, menu playing, "connected", Arabic pairing |
| Tablet screenshots 1440×2560 | `store/screenshots/tablet-portrait-*` | an upright display: pairing, "connected" |
| Phone screenshots 1080×1920 | `store/screenshots/phone-portrait-*` | the same, sized for the phone slot |
| Store listing text, EN + AR | below | |
| Data safety, content rating, target audience answers | below | drafted with the reasoning; you confirm |
| Privacy policy draft | `store/privacy-policy-draft.html` | **needs your legal review and your company details** |
| Graphics regenerator | `store/render_assets.py` | re-renders icons and banners from the mascot art |

The listing graphics and screenshots can be re-made at any time; the TV
screenshots come from the end-to-end pairing test (demo cafe "Karak & Co.").

---

## What only you can do

### 1. Google Play developer account
- [ ] Sign up at https://play.google.com/console with the Google account the
      business will own (not a personal Gmail you might lose). One-time fee
      **US$25**.
- [ ] Choose **Organization** (recommended) rather than Personal:
  - Organization needs a free **D-U-N-S number** for the business
    (https://www.dnb.com/duns.html). Getting one can take a few days to a
    couple of weeks, so start it first.
  - A *Personal* account created today must run a **closed test with at least
    12 testers opted in for 14 days in a row** before it may publish to
    production. Organization accounts skip that.
- [ ] Complete identity verification and add a public developer email
      (hello@khanshoof.com) and, for organizations, a phone number and address.

### 2. Upload key and the first release bundle
- [ ] Run once on this machine:

  ```bash
  android-tv/make-upload-key.sh
  ```

  Set `UPLOAD_KEY_DNAME="CN=…, O=…, C=KW"` first to put your company name in the key.
- [ ] **Back up `android-tv/keystore/`** (both files) in your password manager.
      Google holds the key that signs what users install (Play App Signing);
      this upload key only proves uploads come from you. If it's lost, Play can
      reset it, but that takes days.
- [ ] Build the bundle:

  ```bash
  VERSION_NAME=1.0.0 android-tv/build.sh
  ```

  Upload `dist/khanshoof-tv-1.0.0-<code>-release.aab`. The first upload registers your upload key with Play.
- [ ] Accept **Play App Signing** when asked (it is required for new apps).

> I tested the signed build with a throwaway key and deleted both the key and
> that bundle afterwards, so nothing signed with the wrong key is lying around.

### 3. Privacy policy (required for every app)
- [ ] Review `store/privacy-policy-draft.html`. It describes what the backend
      actually stores (checked in the code). Fill in the bracketed blanks:
      legal entity name and address, data retention periods. Ideally add an
      Arabic version.
- [ ] Publish it as `landing/privacy.html` and deploy the landing site, so it is at
      **https://khanshoof.com/privacy.html**. Link it from the landing footer.
      (I kept the draft out of `landing/` so an unreviewed legal page can't go
      live with the next landing deploy.)

### 4. Create the app in Play Console
- [ ] **Create app** → name `Khanshoof – Digital Signage` · default language
      English (United States) · App · Free.
- [ ] Declarations: follows Developer Program Policies; US export laws.
- [ ] Package name comes from the first upload: `com.khanshoof.tv`
      (permanent).

### 5. Store listing (Grow → Store presence → Main store listing)
- [ ] App name: `Khanshoof – Digital Signage` (27/30 characters)
- [ ] Short description (≤80):
  - EN: `Turn your TV into a menu board. Scan a code with your phone and you're live.`
  - AR: `حوّل تلفزيونك إلى شاشة عرض لقائمتك. امسح الرمز بهاتفك وابدأ فوراً.`
- [ ] Full description (it must say **Android TV**):

  EN:
  > Khanshoof turns any Android TV, Google TV or Android tablet into a digital
  > menu board or sign for your cafe, restaurant or shop.
  >
  > Install the app on the screen, scan the code on it with your phone, sign
  > in, and the screen is paired. From then on you choose what plays from your
  > phone or computer: photos, videos, PDFs, web pages, playlists and
  > schedules for breakfast, lunch and dinner.
  >
  > • Pairs in seconds with a QR code, no typing on the TV remote
  > • Keeps playing when the internet drops: media is cached on the TV
  > • Starts by itself when the screen turns on
  > • Landscape or portrait, TV remote or touch
  > • Recovers on its own from network and app hiccups
  > • English and Arabic
  >
  > Requires a Khanshoof account.

  AR:
  > يحوّل خنشوف أي تلفزيون Android TV أو Google TV أو جهاز أندرويد لوحي إلى شاشة
  > عرض رقمية لقائمة مقهاك أو مطعمك أو متجرك.
  >
  > ثبّت التطبيق على الشاشة، وامسح الرمز الظاهر عليها بهاتفك، وسجّل
  > الدخول، فتُقرن الشاشة. بعدها تختار ما يُعرض من هاتفك أو حاسوبك: صور
  > وفيديوهات وملفات PDF وصفحات ويب وقوائم تشغيل وجداول للفطور والغداء والعشاء.
  >
  > • إقران خلال ثوانٍ برمز QR، بلا كتابة بجهاز التحكم
  > • يواصل العرض عند انقطاع الإنترنت: الوسائط محفوظة على التلفزيون
  > • يبدأ تلقائياً عند تشغيل الشاشة
  > • أفقياً أو عمودياً، بجهاز التحكم أو باللمس
  > • يتعافى وحده من مشاكل الشبكة والتطبيق
  > • بالعربية والإنجليزية
  >
  > يتطلب حساب خنشوف.

  Don't add prices, "free trial" or "subscribe at…" to the listing or the TV
  app: Play's payments policy treats pointing users to outside payment as
  steering. The app only *uses* an account bought elsewhere, which is allowed.
  The player shows no billing text today (checked).
- [ ] Add the Arabic translation: Store listing → Manage translations → Arabic.
- [ ] Graphics: app icon `store/play-icon-512.png`, feature graphic
      `store/play-feature-graphic-1024x500.jpg`.
- [ ] Screenshots:
  - **Phone** (at least 2 are required): `store/screenshots/phone-portrait-*.jpg`
  - **7-inch tablet**: the same phone-portrait files
  - **10-inch tablet**: `tablet-portrait-*.jpg`, plus `tv-*.jpg` for landscape
  - **Android TV** comes in step 6
- [ ] Category: **Business**. Contact email hello@khanshoof.com, website
      https://khanshoof.com, privacy policy URL from step 3.

### 6. Turn on Android TV (Test and release → Advanced settings → Form factors)
- [ ] **Add form factor → Android TV**, opt in.
- [ ] Android TV listing assets: TV banner `store/play-tv-banner-1280x720.jpg`
      and at least one TV screenshot from `store/screenshots/`.
- [ ] Android TV apps get an extra review against the TV quality guidelines
      (self-check below).

### 7. App content forms (Policy → App content)
- [ ] **Privacy policy**: the URL from step 3.
- [ ] **Ads**: No, the app contains no ads.
- [ ] **App access**: *All or some functionality is restricted*. Reviewers
      only see the pairing screen without an account, so give them:
  - a demo account on production (create it in the dashboard; make sure its
    trial doesn't expire mid-review, e.g. an admin-extended subscription)
  - instructions: "Open the app on a TV or tablet. On a phone, scan the QR code (or
    open app.khanshoof.com/pair and type the code), sign in with the account
    below and tap Pair display. The TV shows the demo playlist within
    15 seconds."
  - put a playlist on that account so the paired screen shows something.
- [ ] **Content rating** (IARC questionnaire): category *Utility,
      Productivity, Communication or Other*. Answer no to violence, sexuality,
      language, controlled substances, gambling. Users interacting or sharing
      content: no. Unrestricted internet access: no (viewers can't browse;
      pages shown are chosen by the account owner). Expected: Everyone / 3+.
- [ ] **Target audience**: 18+ (a business tool); not designed for children.
- [ ] **Data safety** (recommended answers; you're legally responsible, so
      read the reasoning):
  - Does the app collect or share user data? **Yes**, one type:
    **Device or other IDs**. The app keeps a random screen token that
    identifies this TV to the business's account. Purpose: app functionality.
    Not shared with third parties, required (can't be turned off).
  - No personal info, location, contacts, photos, app activity, diagnostics
    or advertising ID is collected by the app. The screen has no logged-in
    person: the account is used on the phone, in the web dashboard, not in the
    app.
  - Encrypted in transit: **Yes** (HTTPS only; release builds refuse plain http).
  - Users can request deletion: **Yes**. Deleting the screen in the dashboard
    removes its token. Account deletion requests go to hello@khanshoof.com.
  - Account creation inside the app: **No** (accounts are made on the web).
- [ ] Government app, financial features, health, news, COVID: **No** to all.

### 7b. Displays without the Play Store
- [ ] Many large-format Android displays have no Play Store. After the first
      upload (internal testing is enough), download the **signed universal
      APK** from Play Console → App bundle explorer and offer it as a download,
      e.g. at khanshoof.com/tv. It carries the same signature as Play installs.
      See the README's "Tablets and Android displays without the Play Store"
      section for the WebView-age caveat.

### 8. Countries and price
- [ ] Free. Countries: Kuwait first, then Saudi Arabia, UAE, Qatar, Bahrain,
      Oman as you sell there.

### 9. Test, then release
- [ ] Before uploading, try the debug APK on a real TV **and a tablet** (README →
      "Try it on a TV or tablet"). I couldn't run an Android emulator here (no
      hardware virtualization on this server). Check:
  - the D-pad reaches "Have a code from admin?"
  - the auto-start question appears after pairing
  - unplugging the network shows "Can't reach Khanshoof" and recovers by itself
  - on the tablet: rotating keeps playing without reloading, and a long press selects nothing
- [ ] **Internal testing** track first: upload the `.aab`, add your own Google
      account as tester, install from Play on your TV.
- [ ] Personal account only: closed test with 12+ testers for 14 days (step 1).
- [ ] **Production** → send for review. TV reviews often take longer than
      phone reviews (days, sometimes a couple of weeks).

---

## Android TV quality guidelines: self-check

| Guideline | Status | How |
|---|---|---|
| Leanback launcher entry | ✅ | `LEANBACK_LAUNCHER` intent filter on the main activity |
| Home-screen banner, 320×180, with the app name, localized | ✅ | `drawable-xhdpi/banner.png`, Latin + Arabic name |
| Works without a touchscreen | ✅ | `touchscreen` and `faketouch` declared not required; no touch-only UI |
| D-pad navigation | ⚠️ check on a TV | the pairing page has one control (the admin-code link); WebView moves focus with the D-pad on TVs |
| Back returns to the home screen | ✅ (double press) | BACK twice within 2 s; a reviewer may expect one press, so mention it in the review notes |
| Landscape, full screen, nothing cut off | ✅ | immersive mode; the pairing screen was checked at 1920×1080 and 1280×720 |
| Text readable from across the room | ✅ | pairing code ~13 vh, steps ~2.3 vh (screenshots) |
| No ads, no unrelated system UI | ✅ | |
| Doesn't require hardware TVs lack (camera, GPS, phone) | ✅ | only internet |
| Targets a current API level | ✅ | API 36 (TV requires ≥34) |
| Sound | ✅ | video plays muted; signage needs no audio |

Tablets and large screens:

| Check | Status | How |
|---|---|---|
| Any orientation, no lock | ✅ | no `screenOrientation`; rotation doesn't reload (configChanges); pairing and welcome checked at 9:16 and 16:10 |
| Resizes (split screen, freeform) | ✅ | resizable by default; the player lays itself out to the window |
| Touch | ✅ | buttons work by touch; long press, pinch zoom and overscroll are off |
| Launcher icon | ✅ | adaptive icon with a monochrome layer for themed icons |

## Things a reviewer might raise

- **Phones**: Play also offers the app to phones. That's harmless (a phone is
  a handy way to try it), but to hide it from phones add
  `<supports-screens android:smallScreens="false" android:normalScreens="false" />`
  to the manifest.
- **"Just a WebView of a website"**: Play only forbids webview apps for
  sites you don't own. You own khanshoof.com; be ready to show it (an email
  at the domain, or Search Console). The app also adds kiosk mode, start on
  boot, offline playback and self-recovery.
- **Back button**: the double press is deliberate (a bumped remote mustn't
  blank a menu board). If a reviewer objects, switching to a single press is a
  one-line change in `MainActivity.onBack()`.
- **"Display over other apps"**: declared only so Android 10+ lets the app
  start after boot (Google's documented exception). The app never draws over
  other apps. Say so in the review notes if asked.

## Every release after the first

```bash
VERSION_NAME=1.0.1 android-tv/build.sh
```

The version code rises by itself with each commit. Upload the new `.aab` to a
track. Player changes (`player/`) don't need an app release: TVs pick them up
from play.khanshoof.com.
