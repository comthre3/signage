#!/usr/bin/env bash
# Builds the Khanshoof Android TV app in Docker; needs nothing but Docker.
#
#   android-tv/build.sh               unit tests + lint, then
#                                     dist/khanshoof-tv-<version>-release.aab  (signed, for Play)
#                                     dist/khanshoof-tv-<version>-debug.apk    (for a test TV)
#   android-tv/build.sh --debug-only  tests + lint + the debug APK; no upload key needed
#
# Optional:
#   VERSION_NAME=1.2.0          shown in Play and on the TV (default 1.0.0)
#   VERSION_CODE=123            must grow with every upload (default: git commit count)
#   PLAYER_URL=http://192.168.1.20:3001/   debug APK opens this player instead of production
#   KHAN_UPLOAD_STORE_FILE / _STORE_PASSWORD / _KEY_ALIAS / _KEY_PASSWORD
#                               instead of keystore/keystore.properties; the store file
#                               path is relative to android-tv/
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
image=khanshoof-tv-build

debug_only=0
case "${1:-}" in
  "") ;;
  --debug-only) debug_only=1 ;;
  *) echo "usage: $0 [--debug-only]" >&2; exit 2 ;;
esac

# The Dockerfile installs Gradle; Android Studio users get it from the wrapper.
# Both must be the same Gradle.
wrapper_gradle=$(sed -n 's/^distributionUrl=.*gradle-\([0-9.]*\)-bin\.zip$/\1/p' "$here/gradle/wrapper/gradle-wrapper.properties")
image_gradle=$(sed -n 's/^ARG GRADLE_VERSION=//p' "$here/Dockerfile")
if [[ "$wrapper_gradle" != "$image_gradle" ]]; then
  echo "Gradle $wrapper_gradle in the wrapper but $image_gradle in the Dockerfile; make them match." >&2
  exit 1
fi

version_name="${VERSION_NAME:-1.0.0}"
version_code="${VERSION_CODE:-$(git -C "$repo" rev-list --count HEAD)}"

env_args=()
if [[ $debug_only == 0 ]]; then
  if [[ ! -f "$here/keystore/keystore.properties" && -z "${KHAN_UPLOAD_STORE_FILE:-}" ]]; then
    echo "No upload key. Run android-tv/make-upload-key.sh once, or build with --debug-only." >&2
    exit 1
  fi
  for var in KHAN_UPLOAD_STORE_FILE KHAN_UPLOAD_STORE_PASSWORD KHAN_UPLOAD_KEY_ALIAS KHAN_UPLOAD_KEY_PASSWORD; do
    if [[ -n "${!var:-}" ]]; then env_args+=(-e "$var"); fi
  done
fi

tasks=(testDebugUnitTest lintDebug assembleDebug)
if [[ $debug_only == 0 ]]; then tasks+=(lintRelease bundleRelease); fi

echo "Building the build image (cached after the first time)…"
docker build -q -t "$image" "$here" > /dev/null

# The repository is mounted read-only (the build reads git for the version
# info Play shows); only android-tv/ is writable. Caches stay in .cache/.
mkdir -p "$here/.cache/gradle" "$here/.cache/android" "$here/dist"
in_docker=(docker run --rm --user "$(id -u):$(id -g)"
  -e GRADLE_USER_HOME=/work/android-tv/.cache/gradle
  -e ANDROID_USER_HOME=/work/android-tv/.cache/android
  -e GRADLE_OPTS=-Duser.home=/work/android-tv/.cache
  ${env_args[@]+"${env_args[@]}"}
  -v "$repo:/work:ro" -v "$here:/work/android-tv" -w /work/android-tv
  "$image")

"${in_docker[@]}" gradle --no-daemon --console=plain \
  -PversionCode="$version_code" -PversionName="$version_name" \
  ${PLAYER_URL:+-PplayerUrl="$PLAYER_URL"} \
  "${tasks[@]}"

name="khanshoof-tv-$version_name-$version_code"
cp "$here/app/build/outputs/apk/debug/app-debug.apk" "$here/dist/$name-debug.apk"
outputs=("dist/$name-debug.apk")

if [[ $debug_only == 0 ]]; then
  bundle="app/build/outputs/bundle/release/app-release.aab"
  # An unsigned bundle is useless to Play; never let one pass for a release.
  if ! "${in_docker[@]}" jarsigner -verify "$bundle" > /dev/null; then
    echo "The release bundle is not signed; check the upload key settings." >&2
    exit 1
  fi
  cp "$here/$bundle" "$here/dist/$name-release.aab"
  outputs+=("dist/$name-release.aab")
fi

echo
echo "Built version $version_name ($version_code):"
(cd "$here" && sha256sum "${outputs[@]}")
