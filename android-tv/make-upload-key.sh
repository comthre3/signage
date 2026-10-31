#!/usr/bin/env bash
# Creates the Play upload key, once: android-tv/keystore/upload.jks plus
# keystore.properties with its password. Both are gitignored.
#
# With Play App Signing, Google keeps the key that signs what users install;
# this key only proves an upload came from you. Losing it is recoverable (Play
# Console > Setup > App signing > "Request upload key reset"), but it takes
# days, so back up the whole keystore/ folder somewhere safe (a password
# manager) before you rely on it.
#
#   android-tv/make-upload-key.sh
#   UPLOAD_KEY_DNAME="CN=Your Name, O=Your Company, C=KW" android-tv/make-upload-key.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dir="$here/keystore"
image=khanshoof-tv-build

if [[ -e "$dir/upload.jks" ]]; then
  echo "android-tv/keystore/upload.jks already exists; not replacing your upload key." >&2
  exit 1
fi

mkdir -p "$dir"
chmod 700 "$dir"
password="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-28)"
dname="${UPLOAD_KEY_DNAME:-CN=Khanshoof, O=Khanshoof, C=KW}"

docker build -q -t "$image" "$here" > /dev/null
# PKCS12 keystores use one password for the store and the key.
KS_PASS="$password" docker run --rm --user "$(id -u):$(id -g)" -e KS_PASS \
  -v "$dir:/keystore" "$image" \
  keytool -genkeypair -keystore /keystore/upload.jks -storetype PKCS12 \
    -alias upload -keyalg RSA -keysize 4096 -validity 10000 \
    -storepass:env KS_PASS -keypass:env KS_PASS -dname "$dname"

umask 077
cat > "$dir/keystore.properties" <<EOF
storeFile=keystore/upload.jks
storePassword=$password
keyAlias=upload
keyPassword=$password
EOF
chmod 600 "$dir/upload.jks" "$dir/keystore.properties"

echo "Upload key created in android-tv/keystore/."
echo "Back up that folder (both files) now: you need it for every Play upload."
