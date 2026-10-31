# Review kit: getting Khanshoof approved on Google Play

Google decides, and nobody can promise an approval. What this kit does is
remove every cause of rejection that is in our hands: the app meets Android
TV's published checklist (section 4), and the reviewer gets everything needed
to see it work (sections 2 and 3). Follow [PLAY_STORE_CHECKLIST.md](PLAY_STORE_CHECKLIST.md)
for the account and listing; this file is what to check and paste at review time.

## 1. Before you press "Send for review"

- [ ] Privacy policy is live at https://khanshoof.com/privacy.html (checklist step 3).
- [ ] The demo account works (section 2). Checked on production when it was
      created; check again just before submitting.
- [ ] Production runs the player from this branch (scan-first steps, D-pad
      focus ring, welcome animation). The review works on the older player too,
      but this is the version the checklist was verified against.
- [ ] An **internal testing** release, installed from Play, runs on your own
      TV and a tablet.
- [ ] **Pre-launch report** (Test and release → Pre-launch report) shows no
      crashes. Google runs it on real devices for free after any test upload.
- [ ] TV screenshots are real captures from your TV. Checklist item TV-G4
      asks for "unaltered" screenshots of the TV app. The ones in
      `store/screenshots/` are the same pages rendered by a browser; to
      replace them, install the app, open each screen, then run:

  ```bash
  adb exec-out screencap -p > tv-1.png
  ```
- [ ] Every App content form is answered (section 3).

## 2. The demo account reviewers sign in with

**Created on production 2026-10-31** and tested along the exact path below
(menu on screen 3.5 s after Pair, all three slides rotating): organization
"Khanshoof Review", username `play-review`, screen "Review TV", playlist
"Demo menu". The password is in `android-tv/keystore/play-review-account.txt`
on the server (gitignored, never committed); put it in your password manager.
Delete the organization after approval if you like.

Reviewers can't see anything past the pairing screen without an account
(TV-G5). It is set up so it can't break during a review that may take two weeks:

| | |
|---|---|
| Organization | separate from your real customers, e.g. "Khanshoof Review" |
| Subscription | active, paid well past the review. A trial ends after 5 days, and then pairing is refused, so the reviewer would see an error. |
| Screen | one screen named **Review TV**, already created |
| Playlist | 2–3 **images** that change every 10 s, assigned to Review TV. No videos: media-playback rules (TV-PC, TV-PP, TV-NP) then don't apply. A rotating playlist also counts as activity for the screensaver rule (TV-BY). |
| Login | a username and a long password used nowhere else |

Paste this into **App content → App access → "All or some functionality is
restricted" → Add instructions**, with the real username and password:

```text
Khanshoof is digital signage: the app turns a TV or tablet into a screen that
shows a business's menu. The screen is set up from a phone.

1. Open Khanshoof on the TV or tablet. It shows a 6-letter code and a QR code.
2. On any phone or computer, scan the QR code, or open
   https://app.khanshoof.com/pair and type the code.
3. Sign in. Username: REVIEW_USERNAME   Password: REVIEW_PASSWORD
4. Under "Which screen is this?" choose "Existing screen", pick "Review TV",
   then tap "Pair display".
5. A few seconds later the TV leaves the code screen; within 15 seconds it
   shows the demo menu, a set of images that change every 10 seconds.

BACK on the TV remote returns to the home screen. The app needs only an
internet connection; it has no ads and no in-app purchases.
```

## 3. App content answers

| Form | Answer |
|---|---|
| Privacy policy | https://khanshoof.com/privacy.html |
| App access | Restricted; the instructions from section 2 |
| Ads | No |
| Advertising ID | No. The app doesn't use it: no Play services, and the `AD_ID` permission isn't declared. |
| Content rating | Utility/Productivity/Communication/Other. No to violence, sexuality, language, drugs, gambling, user interaction, and unrestricted internet (viewers can't browse). |
| Target audience | 18 and over; not designed for children |
| Data safety | Collects **Device or other IDs** (the screen token) for app functionality: not shared, required, encrypted in transit, deletable. Nothing else. Details in the checklist, step 7. |
| Government / financial / health / news | No |

## 4. How the app meets Android TV's checklist

The criteria are from the official TV app quality checklist,
https://developer.android.com/docs/quality-guidelines/tv-app-quality
(last updated 2026-06-29). "Tests" names the automated check that covers a
row, where there is one.

| ID | Requirement (short) | Status | How |
|---|---|---|---|
| TV-LM, TV-ML | Shows in the TV launcher; `LEANBACK_LAUNCHER` | ✅ | main activity's intent filter |
| TV-LB | 320×180 banner + icon ≥160×160 px at xhdpi | ✅ | `drawable-xhdpi/banner.png`; `mipmap-xhdpi/ic_launcher.png` is 160×160, the adaptive icon 216 px |
| TV-BN | Banner shows the app name | ✅ | "Khanshoof" + "خنشوف" |
| TV-LS | Runs without errors | ✅ / device | Robolectric launch tests on TV and tablet; confirm on a real TV |
| TV-LO | Landscape, no letterboxing except black bars on media | ✅ | full-screen pages; media letterboxing is black |
| TV-OV | Nothing cut off at the edges | ✅ | content kept away from the edges; checked at 1920×1080 and 1280×720 |
| TV-TR | Fills the screen, opaque | ✅ | immersive, opaque backgrounds |
| TV-DP | Everything works with the D-pad | ✅ | the pairing screen's only control is reachable and shows a 4 px focus ring (harness step 11) |
| TV-DM | No Menu button needed | ✅ | |
| TV-DB | BACK leads to the home screen | ✅ | one press on TVs (test `onATvThePlayerOpensAndOneBackGoesHome`); two only on touch screens |
| TV-PS | minSdk ≤ 31 | ✅ | minSdk 23 |
| TV-MT | Touchscreen etc. not required | ✅ | touchscreen, faketouch, leanback all optional; no permission implies hardware |
| TV-WB | WebView only, never launches a browser | ✅ | other sites are ignored, never opened |
| TV-NP | Video paused when leaving the app | ✅ | leaving pauses the player's videos and timers; they resume on return |
| TV-PC, TV-PP | Play/pause keys during playback | n/a for review | signage plays muted clips unattended; the demo playlist uses images only |
| TV-BU, TV-BY | Ambient mode only blocked during activity | ✅ | content rotates; the pairing, welcome and error screens animate their mascot; the unsupported-TV screen lets the TV sleep |
| TV-ME | Memory on low-RAM TVs | not measured | no device here; check on a cheap TV if you can |
| TV-G1 | App bundle | ✅ | `build.sh` makes the `.aab` |
| TV-G4 | Unaltered TV screenshot | ⚠️ you | replace with real captures (section 1) |
| TV-G5 | Login for review | ⚠️ you | demo account (section 2) |
| TV-G6 | 32/64-bit + 16 KB pages (from 2026-08-01) | ✅ | no native code in the app |
| TV-G2, TV-G3 | Follows policy; works as described | ✅ | listing text matches what the app does |
| Games, ads, PiP, live TV rows | | n/a | |

Policy points outside that checklist:

- **WebView apps**: Play refuses webview wrappers of sites the developer
  doesn't own, and apps with no value beyond a browser tab. You own
  khanshoof.com, and the app adds kiosk mode, start on boot, offline playback
  and self-recovery.
- **Payments**: nothing is sold in the app and it never points to a purchase.
  Keep prices and "subscribe" out of the listing and the player.
- **Target API**: 36, above Play's minimum for TV (34) and for phones and
  tablets (36).

## 5. If Google rejects it anyway

The email names a policy or a TV-xx criterion. Fix it if it's real, or reply
from the Policy status page with an explanation. Ready answers:

- **"Couldn't test / login failed"**: sign in with the demo account yourself.
  Is its subscription still active? Is Review TV still there with the playlist?
- **"Webview / minimum functionality"**: "We own khanshoof.com (the app's
  only web content). The app is a kiosk player for digital signage: it keeps
  the screen on and full screen, starts after power cuts, keeps playing
  offline from its cache and recovers from crashes and network loss by itself."
- **SYSTEM_ALERT_WINDOW**: "Declared only so the app may open itself after the
  device boots, which Android 10+ allows for apps holding this permission
  (developer.android.com/guide/components/activities/background-starts). The
  app never draws over other apps. It asks once, after pairing, and only on
  devices that have that settings screen."
- **Something on a device you don't have**: the pre-launch report has
  screenshots and logs per device.
