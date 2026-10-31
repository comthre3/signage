# Build environment for the Khanshoof Android TV app.
#
# Everything the build reads is pinned: the JDK image by digest, the Android
# command-line tools and Gradle by version and SHA-256, the SDK packages by
# name. Gradle plugins and libraries are pinned in the build files. Two builds
# of the same commit therefore compile with the same toolchain; only the
# signatures differ (they carry timestamps).
#
# Build:  docker build -t khanshoof-tv-build android-tv
# Use:    android-tv/build.sh  (does both)
FROM eclipse-temurin:17-jdk-jammy@sha256:81336a07f69ac361c7f278183b195e32037ebdb532684996806265a3b47ef516

# Google lists the SHA-1 of each cmdline-tools zip in
# https://dl.google.com/android/repository/repository2-3.xml; the SHA-256 below
# is of the file whose SHA-1 matches that listing.
ARG CMDLINE_TOOLS_BUILD=15859902
ARG CMDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
# Must match distributionUrl in gradle/wrapper/gradle-wrapper.properties
# (build.sh checks); the SHA-256 is Gradle's own published checksum.
ARG GRADLE_VERSION=8.14.5
ARG GRADLE_SHA256=6f74b601422d6d6fc4e1f9a1ab6522f642c2fdcbc15ae33ebd30ba3d7198e854
ARG ANDROID_PLATFORM=android-36
ARG ANDROID_BUILD_TOOLS=35.0.0

ENV ANDROID_HOME=/opt/android-sdk \
    GRADLE_HOME=/opt/gradle
ENV PATH=/opt/gradle/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/build-tools/${ANDROID_BUILD_TOOLS}:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip git \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/clt.zip \
      "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_BUILD}_latest.zip" \
 && echo "${CMDLINE_TOOLS_SHA256}  /tmp/clt.zip" | sha256sum -c - \
 && mkdir -p "$ANDROID_HOME/cmdline-tools" \
 && unzip -q /tmp/clt.zip -d "$ANDROID_HOME/cmdline-tools" \
 && mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" \
 && rm /tmp/clt.zip

RUN yes | sdkmanager --licenses > /dev/null \
 && sdkmanager --install "platforms;${ANDROID_PLATFORM}" "build-tools;${ANDROID_BUILD_TOOLS}" "platform-tools" > /dev/null \
 && chmod -R a+rX "$ANDROID_HOME"

RUN curl -fsSL -o /tmp/gradle.zip \
      "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
 && echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum -c - \
 && unzip -q /tmp/gradle.zip -d /opt \
 && mv "/opt/gradle-${GRADLE_VERSION}" /opt/gradle \
 && rm /tmp/gradle.zip

WORKDIR /work
