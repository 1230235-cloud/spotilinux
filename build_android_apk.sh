#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_DIR="$BASE_DIR/android-app"
BUILD_DIR="$ANDROID_DIR/build"
DIST_DIR="$BASE_DIR/dist"
SDK_DIR="$BASE_DIR/.android-sdk"

PLATFORM_JAR="$SDK_DIR/platforms/android-34/android.jar"
BUILD_TOOLS="$SDK_DIR/build-tools/34.0.0"

if [ ! -f "$PLATFORM_JAR" ] || [ ! -d "$BUILD_TOOLS" ]; then
    echo "Error: Android SDK platform 34 o build-tools 34.0.0 no encontrados en $SDK_DIR"
    exit 1
fi

export JAVA_HOME="/usr/lib/jvm/java-17-openjdk"
export PATH="$JAVA_HOME/bin:$BUILD_TOOLS:$PATH"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/gen" "$BUILD_DIR/obj" "$BUILD_DIR/dex" "$DIST_DIR"

echo "[1/6] Compilando recursos Android con aapt2..."
"$BUILD_TOOLS/aapt2" compile --dir "$ANDROID_DIR/res" -o "$BUILD_DIR/resources.zip"

echo "[2/6] Enlazando recursos y AndroidManifest.xml..."
"$BUILD_TOOLS/aapt2" link \
    -I "$PLATFORM_JAR" \
    --manifest "$ANDROID_DIR/AndroidManifest.xml" \
    -A "$ANDROID_DIR/assets" \
    --java "$BUILD_DIR/gen" \
    -o "$BUILD_DIR/unaligned.apk" \
    "$BUILD_DIR/resources.zip"

echo "[3/6] Compilando código Java (MainActivity.java + MediaPlaybackService.java + GenericFileProvider.java + R.java)..."
JAVA_SOURCES=$(find "$BUILD_DIR/gen" "$ANDROID_DIR/src" -name "*.java")
"$JAVA_HOME/bin/javac" \
    --release 11 \
    -cp "$PLATFORM_JAR" \
    -d "$BUILD_DIR/obj" \
    $JAVA_SOURCES

echo "[4/6] Convirtiendo clases Java a bytecode Dalvik (classes.dex) con d8..."
CLASS_FILES=$(find "$BUILD_DIR/obj" -name "*.class")
"$BUILD_TOOLS/d8" \
    --lib "$PLATFORM_JAR" \
    --output "$BUILD_DIR/dex" \
    $CLASS_FILES

echo "[5/6] Empaquetando classes.dex y alineando con zipalign..."
(cd "$BUILD_DIR/dex" && zip -q -u "$BUILD_DIR/unaligned.apk" classes.dex)
"$BUILD_TOOLS/zipalign" -f -p 4 "$BUILD_DIR/unaligned.apk" "$BUILD_DIR/aligned.apk"

echo "[6/6] Firmando APK con apksigner..."
KEYSTORE="$ANDROID_DIR/debug.keystore"
if [ ! -f "$KEYSTORE" ]; then
    "$JAVA_HOME/bin/keytool" -genkeypair \
        -v -keystore "$KEYSTORE" \
        -storepass android -keypass android \
        -alias androiddebugkey \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -dname "CN=SpotiLinux,OU=Mobile,O=SpotiLinux,L=Linux,S=Linux,C=MX" >/dev/null 2>&1
fi

FINAL_APK="$DIST_DIR/MaoMusic.apk"
"$BUILD_TOOLS/apksigner" sign \
    --ks "$KEYSTORE" \
    --ks-pass pass:android \
    --key-pass pass:android \
    --out "$FINAL_APK" \
    "$BUILD_DIR/aligned.apk"

cp -f "$FINAL_APK" "$BASE_DIR/MaoMusic.apk"
cp -f "$FINAL_APK" "$DIST_DIR/SpotiLinux-Mobile.apk"
cp -f "$FINAL_APK" "$BASE_DIR/SpotiLinux-Mobile.apk"
echo "✔ ¡APK de Android (MaoMusic) generado y firmado exitosamente en: $FINAL_APK!"
