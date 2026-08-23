#!/bin/bash
set -euo pipefail

KD_MAC_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KD_MAC_VERSION="${KD_MAC_VERSION:-0.2.0}"
KD_MAC_BUILD_NUMBER="${KD_MAC_BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"
KD_MAC_TEMP_ROOT="${TMPDIR:-/tmp}"
KD_MAC_BUILD_ROOT="${KD_MAC_BUILD_ROOT:-${KD_MAC_TEMP_ROOT%/}/knowledgedebt-macos-build-$(id -u)}"
while [[ "$KD_MAC_BUILD_ROOT" == */ ]]; do
  KD_MAC_BUILD_ROOT="${KD_MAC_BUILD_ROOT%/}"
done
KD_MAC_DIST_ROOT="${KD_MAC_DIST_ROOT:-$KD_MAC_ROOT/dist/macos}"
KD_MAC_CACHE_ROOT="${KD_MAC_CACHE_ROOT:-$KD_MAC_ROOT/.cache/macos-packaging}"
KD_MAC_PYTHON_ENV="$KD_MAC_BUILD_ROOT/python-env"
KD_MAC_PYTHON="$KD_MAC_PYTHON_ENV/bin/python"
KD_MAC_APP="$KD_MAC_BUILD_ROOT/app/KnowledgeDebt.app"
KD_MAC_DIST_APP="$KD_MAC_DIST_ROOT/KnowledgeDebt.app"
KD_MAC_NODE_VERSION="24.19.0"
KD_MAC_NODE_ARCHIVE="node-v$KD_MAC_NODE_VERSION-darwin-arm64.tar.gz"
KD_MAC_NODE_SHA256="8294b7aa9b03997481c06babf1e8b270c859358f27da57a11509afe537ac381d"
KD_MAC_FFMPEG_VERSION="9.0.1"
KD_MAC_FFMPEG_ARCHIVE="ffmpeg-$KD_MAC_FFMPEG_VERSION.tar.xz"
KD_MAC_FFMPEG_SHA256="cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635"
KD_MAC_FFMPEG_BUILD_ID="$KD_MAC_FFMPEG_VERSION-arm64-static-audio-stable-prefix-v1"
KD_MAC_FFMPEG_INSTALL="$KD_MAC_BUILD_ROOT/ffmpeg-stage/KnowledgeDebt/runtime/ffmpeg"
KD_MAC_FFMPEG_STAMP="$KD_MAC_BUILD_ROOT/ffmpeg-stage/.build-id"
KD_MAC_CMAKE_VERSION="4.1.0"
KD_MAC_CMAKE_ARCHIVE="cmake-$KD_MAC_CMAKE_VERSION-macos-universal.tar.gz"
KD_MAC_CMAKE_SHA256="08cbe0b807799a90216923acf457481538c8d0608a19ba9203219387427a4055"
KD_MAC_WHISPER_TAG="b4938"
KD_MAC_WHISPER_ARCHIVE="whisper.cpp-$KD_MAC_WHISPER_TAG.tar.gz"
KD_MAC_WHISPER_SHA256="6d8d70a014ca2b10f8a6d006b8f423e5f5ef2afcfbe92b57ab4e01107238112a"
KD_MAC_WHISPER_BUILD_ID="$KD_MAC_WHISPER_TAG-arm64-macos13-static-metal-noblas-pathmap-v3"
KD_MAC_VAD_REVISION="9ffd54a1e1ee413ddf265af9913beaf518d1639b"
KD_MAC_VAD_FILE="ggml-silero-v6.2.0.bin"
KD_MAC_VAD_SHA256="2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987"

case "$KD_MAC_VERSION" in
  *[!0-9A-Za-z._-]*|'') echo "KD_MAC_VERSION 只能包含数字、字母、点、下划线或连字符" >&2; exit 2 ;;
esac
case "$KD_MAC_BUILD_NUMBER" in
  *[!0-9A-Za-z._-]*|'') echo "KD_MAC_BUILD_NUMBER 格式无效" >&2; exit 2 ;;
esac
case "$KD_MAC_BUILD_ROOT" in
  ''|'/') echo "KD_MAC_BUILD_ROOT 不能是空路径或文件系统根目录" >&2; exit 2 ;;
  *' '*) echo "FFmpeg 临时构建目录不能包含空格：$KD_MAC_BUILD_ROOT" >&2; exit 2 ;;
esac

for command in curl make npm shasum tar xcrun hdiutil ditto codesign file otool xattr; do
  command -v "$command" >/dev/null || { echo "构建缺少命令：$command" >&2; exit 1; }
done
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || {
  echo "当前脚本只构建 macOS Apple Silicon 安装包" >&2
  exit 1
}
[[ -x "$KD_MAC_ROOT/.venv/bin/python" ]] || {
  echo "缺少项目虚拟环境，请先运行 ./start.sh 完成依赖初始化，然后按 Ctrl+C 停止服务" >&2
  exit 1
}

download_verified() {
  local url="$1" expected="$2" target="$3" temporary fresh
  mkdir -p "$(dirname "$target")"
  if [[ -f "$target" ]] && echo "$expected  $target" | shasum -a 256 -c - >/dev/null 2>&1; then
    return
  fi
  temporary="$target.download"
  fresh="$temporary.full"
  if [[ -s "$temporary" ]] && curl --fail --location --retry 2 --retry-all-errors \
    --retry-delay 2 --continue-at - --output "$temporary" "$url"; then
    :
  else
    curl --fail --location --retry 5 --retry-all-errors --retry-delay 2 \
      --output "$fresh" "$url"
    mv "$fresh" "$temporary"
  fi
  if ! echo "$expected  $temporary" | shasum -a 256 -c -; then
    echo "断点文件校验失败，重新下载完整归档：$(basename "$target")" >&2
    curl --fail --location --retry 5 --retry-all-errors --retry-delay 2 \
      --output "$fresh" "$url"
    echo "$expected  $fresh" | shasum -a 256 -c -
    mv "$fresh" "$temporary"
  fi
  mv "$temporary" "$target"
}

echo "准备经过哈希校验的 Node.js $KD_MAC_NODE_VERSION"
download_verified \
  "https://nodejs.org/dist/v$KD_MAC_NODE_VERSION/$KD_MAC_NODE_ARCHIVE" \
  "$KD_MAC_NODE_SHA256" \
  "$KD_MAC_CACHE_ROOT/$KD_MAC_NODE_ARCHIVE"

echo "准备 LGPL 配置的 FFmpeg $KD_MAC_FFMPEG_VERSION"
download_verified \
  "https://ffmpeg.org/releases/$KD_MAC_FFMPEG_ARCHIVE" \
  "$KD_MAC_FFMPEG_SHA256" \
  "$KD_MAC_CACHE_ROOT/$KD_MAC_FFMPEG_ARCHIVE"

echo "准备仅用于构建的官方 CMake $KD_MAC_CMAKE_VERSION"
download_verified \
  "https://github.com/Kitware/CMake/releases/download/v$KD_MAC_CMAKE_VERSION/$KD_MAC_CMAKE_ARCHIVE" \
  "$KD_MAC_CMAKE_SHA256" \
  "$KD_MAC_CACHE_ROOT/$KD_MAC_CMAKE_ARCHIVE"

echo "准备 whisper.cpp $KD_MAC_WHISPER_TAG 固定源码"
download_verified \
  "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/$KD_MAC_WHISPER_TAG.tar.gz" \
  "$KD_MAC_WHISPER_SHA256" \
  "$KD_MAC_CACHE_ROOT/$KD_MAC_WHISPER_ARCHIVE"

echo "准备经过哈希校验的 Silero VAD $KD_MAC_VAD_FILE"
download_verified \
  "https://huggingface.co/ggml-org/whisper-vad/resolve/$KD_MAC_VAD_REVISION/$KD_MAC_VAD_FILE?download=true" \
  "$KD_MAC_VAD_SHA256" \
  "$KD_MAC_CACHE_ROOT/$KD_MAC_VAD_FILE"

mkdir -p "$KD_MAC_BUILD_ROOT" "$KD_MAC_DIST_ROOT"
KD_MAC_PYTHON_BUILD_ID="$(
  {
    "$KD_MAC_ROOT/.venv/bin/python" --version
    shasum -a 256 \
      "$KD_MAC_ROOT/backend/requirements.txt" \
      "$KD_MAC_ROOT/backend/requirements-dev.txt"
  } | shasum -a 256 | awk '{print $1}'
)"
if [[ ! -x "$KD_MAC_PYTHON_ENV/bin/pyinstaller" ]] \
  || [[ ! -f "$KD_MAC_PYTHON_ENV/.build-id" ]] \
  || [[ "$(<"$KD_MAC_PYTHON_ENV/.build-id")" != "$KD_MAC_PYTHON_BUILD_ID" ]]; then
  echo "准备独立于同步目录的打包 Python 环境"
  rm -rf "$KD_MAC_PYTHON_ENV"
  "$KD_MAC_ROOT/.venv/bin/python" -m venv "$KD_MAC_PYTHON_ENV"
  "$KD_MAC_PYTHON_ENV/bin/pip" install \
    --disable-pip-version-check \
    -r "$KD_MAC_ROOT/backend/requirements-dev.txt"
  printf '%s\n' "$KD_MAC_PYTHON_BUILD_ID" > "$KD_MAC_PYTHON_ENV/.build-id"
else
  echo "复用打包 Python 环境"
fi
rm -rf \
  "$KD_MAC_BUILD_ROOT/app" \
  "$KD_MAC_BUILD_ROOT/backend-dist" \
  "$KD_MAC_BUILD_ROOT/node" \
  "$KD_MAC_BUILD_ROOT/pyinstaller" \
  "$KD_MAC_BUILD_ROOT/spec" \
  "$KD_MAC_DIST_APP" \
  "$KD_MAC_DIST_ROOT/KnowledgeDebt-$KD_MAC_VERSION-arm64.dmg" \
  "$KD_MAC_DIST_ROOT/KnowledgeDebt-$KD_MAC_VERSION-arm64.zip"

mkdir -p "$KD_MAC_BUILD_ROOT/node"
tar -xzf "$KD_MAC_CACHE_ROOT/$KD_MAC_NODE_ARCHIVE" -C "$KD_MAC_BUILD_ROOT/node" --strip-components=1

if [[ -x "$KD_MAC_FFMPEG_INSTALL/bin/ffmpeg" ]] \
  && [[ -x "$KD_MAC_FFMPEG_INSTALL/bin/ffprobe" ]] \
  && [[ -f "$KD_MAC_FFMPEG_STAMP" ]] \
  && [[ "$(<"$KD_MAC_FFMPEG_STAMP")" == "$KD_MAC_FFMPEG_BUILD_ID" ]] \
  && "$KD_MAC_FFMPEG_INSTALL/bin/ffmpeg" -version 2>/dev/null | head -1 | grep -q "ffmpeg version $KD_MAC_FFMPEG_VERSION" \
  && "$KD_MAC_FFMPEG_INSTALL/bin/ffmpeg" -filters 2>/dev/null | grep -q ' concat '; then
  echo "复用已验证的受控 FFmpeg 构建"
else
  rm -rf \
    "$KD_MAC_BUILD_ROOT/ffmpeg-build" \
    "$KD_MAC_BUILD_ROOT/ffmpeg-stage" \
    "$KD_MAC_BUILD_ROOT/ffmpeg-source"
  mkdir -p \
    "$KD_MAC_BUILD_ROOT/ffmpeg-build" \
    "$KD_MAC_BUILD_ROOT/ffmpeg-stage" \
    "$KD_MAC_BUILD_ROOT/ffmpeg-source"
  tar -xJf "$KD_MAC_CACHE_ROOT/$KD_MAC_FFMPEG_ARCHIVE" -C "$KD_MAC_BUILD_ROOT/ffmpeg-source" --strip-components=1
  (
    cd "$KD_MAC_BUILD_ROOT/ffmpeg-build"
    "$KD_MAC_BUILD_ROOT/ffmpeg-source/configure" \
      --prefix=/KnowledgeDebt/runtime/ffmpeg \
      --arch=arm64 \
      --cc=clang \
      --disable-autodetect \
      --disable-debug \
      --disable-doc \
      --disable-network \
      --disable-shared \
      --enable-static \
      --disable-everything \
      --enable-ffmpeg \
      --enable-ffprobe \
      --enable-avcodec \
      --enable-avfilter \
      --enable-avformat \
      --enable-swresample \
      --enable-protocol=file,pipe \
      --enable-demuxer=aac,concat,flac,matroska,mov,mp3,ogg,wav \
      --enable-muxer=flac,matroska,webm,wav \
      --enable-decoder=aac,aac_fixed,alac,flac,mp3,opus,pcm_f32le,pcm_s16le,pcm_s24le,vorbis \
      --enable-encoder=flac,pcm_s16le \
      --enable-parser=aac,flac,mpegaudio,opus,vorbis \
      --enable-filter=aformat,anull,aresample,atrim,concat
    make -j"$(sysctl -n hw.logicalcpu)"
    make DESTDIR="$KD_MAC_BUILD_ROOT/ffmpeg-stage" install
  )
  printf '%s\n' "$KD_MAC_FFMPEG_BUILD_ID" > "$KD_MAC_FFMPEG_STAMP"
fi

for binary in ffmpeg ffprobe; do
  if otool -L "$KD_MAC_FFMPEG_INSTALL/bin/$binary" | grep -E '/opt/homebrew|/usr/local|/Users/' >/dev/null; then
    echo "FFmpeg 构建仍依赖开发机路径：$binary" >&2
    exit 1
  fi
done

KD_MAC_CMAKE_ROOT="$KD_MAC_BUILD_ROOT/cmake"
KD_MAC_CMAKE="$KD_MAC_CMAKE_ROOT/CMake.app/Contents/bin/cmake"
if [[ ! -x "$KD_MAC_CMAKE" ]]; then
  rm -rf "$KD_MAC_CMAKE_ROOT"
  mkdir -p "$KD_MAC_CMAKE_ROOT"
  tar -xzf "$KD_MAC_CACHE_ROOT/$KD_MAC_CMAKE_ARCHIVE" -C "$KD_MAC_CMAKE_ROOT" --strip-components=1
fi
[[ -x "$KD_MAC_CMAKE" ]] || { echo "CMake 官方归档结构异常" >&2; exit 1; }

KD_MAC_WHISPER_INSTALL="$KD_MAC_BUILD_ROOT/whisper-install"
KD_MAC_WHISPER_BINARY="$KD_MAC_WHISPER_INSTALL/bin/whisper-cli"
KD_MAC_WHISPER_STAMP="$KD_MAC_WHISPER_INSTALL/.build-id"
if [[ -x "$KD_MAC_WHISPER_BINARY" ]] \
  && [[ -f "$KD_MAC_WHISPER_STAMP" ]] \
  && [[ "$(<"$KD_MAC_WHISPER_STAMP")" == "$KD_MAC_WHISPER_BUILD_ID" ]] \
  && file -b "$KD_MAC_WHISPER_BINARY" | grep -q 'arm64' \
  && ! otool -L "$KD_MAC_WHISPER_BINARY" | grep -E '/opt/homebrew|/usr/local|/Users/' >/dev/null; then
  echo "复用已验证的 whisper.cpp Apple Silicon 构建"
else
  rm -rf \
    "$KD_MAC_BUILD_ROOT/whisper-build" \
    "$KD_MAC_WHISPER_INSTALL" \
    "$KD_MAC_BUILD_ROOT/whisper-source"
  mkdir -p "$KD_MAC_BUILD_ROOT/whisper-source" "$KD_MAC_WHISPER_INSTALL/bin"
  tar -xzf "$KD_MAC_CACHE_ROOT/$KD_MAC_WHISPER_ARCHIVE" \
    -C "$KD_MAC_BUILD_ROOT/whisper-source" --strip-components=1
  "$KD_MAC_CMAKE" \
    -S "$KD_MAC_BUILD_ROOT/whisper-source" \
    -B "$KD_MAC_BUILD_ROOT/whisper-build" \
    -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_NATIVE=OFF \
    -DGGML_BLAS=OFF \
    -DGGML_CCACHE=OFF \
    -DGGML_METAL=ON \
    -DGGML_METAL_EMBED_LIBRARY=ON \
    -DGGML_ACCELERATE=ON \
    -DWHISPER_BUILD_IS_DEV=OFF \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON \
    -DWHISPER_BUILD_SERVER=OFF \
    -DWHISPER_CURL=OFF \
    -DWHISPER_SDL2=OFF \
    -DCMAKE_C_FLAGS="-ffile-prefix-map=$KD_MAC_BUILD_ROOT=/build -fdebug-prefix-map=$KD_MAC_BUILD_ROOT=/build" \
    -DCMAKE_CXX_FLAGS="-ffile-prefix-map=$KD_MAC_BUILD_ROOT=/build -fdebug-prefix-map=$KD_MAC_BUILD_ROOT=/build"
  "$KD_MAC_CMAKE" --build "$KD_MAC_BUILD_ROOT/whisper-build" \
    --target whisper-cli --parallel "$(sysctl -n hw.logicalcpu)"
  cp "$KD_MAC_BUILD_ROOT/whisper-build/bin/whisper-cli" "$KD_MAC_WHISPER_BINARY"
  printf '%s\n' "$KD_MAC_WHISPER_BUILD_ID" > "$KD_MAC_WHISPER_STAMP"
fi
file -b "$KD_MAC_WHISPER_BINARY" | grep -q 'arm64'
if otool -L "$KD_MAC_WHISPER_BINARY" | grep -E '/opt/homebrew|/usr/local|/Users/' >/dev/null; then
  echo "whisper.cpp 构建仍依赖开发机路径" >&2
  otool -L "$KD_MAC_WHISPER_BINARY" >&2
  exit 1
fi
if [[ ! -f "$KD_MAC_BUILD_ROOT/whisper-source/LICENSE" ]]; then
  mkdir -p "$KD_MAC_BUILD_ROOT/whisper-source"
  tar -xzf "$KD_MAC_CACHE_ROOT/$KD_MAC_WHISPER_ARCHIVE" \
    -C "$KD_MAC_BUILD_ROOT/whisper-source" --strip-components=1
fi

echo "冻结 FastAPI 后端"
"$KD_MAC_PYTHON_ENV/bin/pyinstaller" \
  --clean \
  --noconfirm \
  --onedir \
  --name knowledgedebt-api \
  --paths "$KD_MAC_ROOT/backend" \
  --distpath "$KD_MAC_BUILD_ROOT/backend-dist" \
  --workpath "$KD_MAC_BUILD_ROOT/pyinstaller" \
  --specpath "$KD_MAC_BUILD_ROOT/spec" \
  --collect-all fitz \
  --collect-all pymupdf \
  --collect-all uvicorn \
  --hidden-import app.orm \
  "$KD_MAC_ROOT/packaging/macos/backend_entry.py"

echo "构建 Next.js standalone"
(
  cd "$KD_MAC_ROOT/web"
  npm ci
  npm run lint
  npm run build
)

KD_MAC_CONTENTS="$KD_MAC_APP/Contents"
KD_MAC_RESOURCES="$KD_MAC_CONTENTS/Resources"
mkdir -p \
  "$KD_MAC_CONTENTS/MacOS" \
  "$KD_MAC_RESOURCES/backend" \
  "$KD_MAC_RESOURCES/runtime/node/bin" \
  "$KD_MAC_RESOURCES/runtime/ffmpeg/bin" \
  "$KD_MAC_RESOURCES/runtime/whisper/bin" \
  "$KD_MAC_RESOURCES/runtime/whisper/models" \
  "$KD_MAC_RESOURCES/web" \
  "$KD_MAC_RESOURCES/LICENSES" \
  "$KD_MAC_RESOURCES/DOCUMENTATION"

cp -R "$KD_MAC_BUILD_ROOT/backend-dist/knowledgedebt-api/." "$KD_MAC_RESOURCES/backend/"
cp "$KD_MAC_BUILD_ROOT/node/bin/node" "$KD_MAC_RESOURCES/runtime/node/bin/node"
cp "$KD_MAC_FFMPEG_INSTALL/bin/ffmpeg" "$KD_MAC_RESOURCES/runtime/ffmpeg/bin/ffmpeg"
cp "$KD_MAC_FFMPEG_INSTALL/bin/ffprobe" "$KD_MAC_RESOURCES/runtime/ffmpeg/bin/ffprobe"
cp "$KD_MAC_WHISPER_BINARY" "$KD_MAC_RESOURCES/runtime/whisper/bin/whisper-cli"
cp "$KD_MAC_CACHE_ROOT/$KD_MAC_VAD_FILE" "$KD_MAC_RESOURCES/runtime/whisper/models/$KD_MAC_VAD_FILE"
cp "$KD_MAC_ROOT/.env.example" "$KD_MAC_RESOURCES/DOCUMENTATION/DEFAULT_CONFIGURATION.env.example"
cp "$KD_MAC_ROOT/README.md" "$KD_MAC_RESOURCES/DOCUMENTATION/README.zh-CN.md"
cp "$KD_MAC_ROOT/docs/macos.md" "$KD_MAC_RESOURCES/DOCUMENTATION/macOS-安装与数据管理.md"
cp "$KD_MAC_ROOT/docs/providers.md" "$KD_MAC_RESOURCES/DOCUMENTATION/Provider-能力矩阵.md"
cp "$KD_MAC_ROOT/docs/release-v0.2.md" "$KD_MAC_RESOURCES/DOCUMENTATION/v0.2-发布与验收.md"
cp -R "$KD_MAC_ROOT/web/.next/standalone/." "$KD_MAC_RESOURCES/web/"
mkdir -p "$KD_MAC_RESOURCES/web/.next"
cp -R "$KD_MAC_ROOT/web/.next/static" "$KD_MAC_RESOURCES/web/.next/static"
if [[ -d "$KD_MAC_ROOT/web/public" ]]; then
  cp -R "$KD_MAC_ROOT/web/public" "$KD_MAC_RESOURCES/web/public"
fi
cp -R "$KD_MAC_ROOT/backend/alembic" "$KD_MAC_RESOURCES/backend/alembic"
find "$KD_MAC_RESOURCES/backend/alembic" -type f -name '*.pyc' -delete
find "$KD_MAC_RESOURCES/backend/alembic" -type d -name '__pycache__' -empty -delete
"$KD_MAC_PYTHON" "$KD_MAC_ROOT/packaging/macos/sanitize_next_standalone.py" \
  --web-root "$KD_MAC_RESOURCES/web" \
  --source-root "$KD_MAC_ROOT/web"

sed \
  -e "s/@VERSION@/$KD_MAC_VERSION/g" \
  -e "s/@BUILD_NUMBER@/$KD_MAC_BUILD_NUMBER/g" \
  "$KD_MAC_ROOT/packaging/macos/Info.plist.in" > "$KD_MAC_CONTENTS/Info.plist"

xcrun swiftc \
  -swift-version 5 \
  -O \
  -target arm64-apple-macos13.0 \
  -framework AppKit \
  -framework Security \
  "$KD_MAC_ROOT/packaging/macos/Launcher.swift" \
  -o "$KD_MAC_CONTENTS/MacOS/KnowledgeDebt"

KD_MAC_ICONSET="$KD_MAC_BUILD_ROOT/AppIcon.iconset"
rm -rf "$KD_MAC_ICONSET"
mkdir -p "$KD_MAC_ICONSET"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_16.png" "$KD_MAC_ICONSET/icon_16x16.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_32.png" "$KD_MAC_ICONSET/icon_16x16@2x.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_32.png" "$KD_MAC_ICONSET/icon_32x32.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_64.png" "$KD_MAC_ICONSET/icon_32x32@2x.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_128.png" "$KD_MAC_ICONSET/icon_128x128.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_256.png" "$KD_MAC_ICONSET/icon_128x128@2x.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_256.png" "$KD_MAC_ICONSET/icon_256x256.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_512.png" "$KD_MAC_ICONSET/icon_256x256@2x.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_512.png" "$KD_MAC_ICONSET/icon_512x512.png"
cp "$KD_MAC_ROOT/legacy/flutter-client/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_1024.png" "$KD_MAC_ICONSET/icon_512x512@2x.png"
iconutil -c icns "$KD_MAC_ICONSET" -o "$KD_MAC_RESOURCES/AppIcon.icns"

cp "$KD_MAC_ROOT/LICENSE" "$KD_MAC_RESOURCES/LICENSES/KnowledgeDebt-MIT.txt"
cp "$KD_MAC_BUILD_ROOT/node/LICENSE" "$KD_MAC_RESOURCES/LICENSES/Node.js-MIT.txt"
cp "$KD_MAC_BUILD_ROOT/ffmpeg-source/COPYING.LGPLv2.1" "$KD_MAC_RESOURCES/LICENSES/FFmpeg-LGPLv2.1.txt"
cp "$KD_MAC_BUILD_ROOT/whisper-source/LICENSE" "$KD_MAC_RESOURCES/LICENSES/whisper.cpp-MIT.txt"
cp "$KD_MAC_ROOT/packaging/macos/SILERO_VAD_LICENSE.txt" "$KD_MAC_RESOURCES/LICENSES/Silero-VAD-MIT.txt"
cp "$KD_MAC_ROOT/packaging/macos/THIRD_PARTY_NOTICES.md" "$KD_MAC_RESOURCES/LICENSES/THIRD_PARTY_NOTICES.md"
"$KD_MAC_PYTHON" "$KD_MAC_ROOT/packaging/macos/generate_notices.py" \
  --web-root "$KD_MAC_RESOURCES/web" \
  --output "$KD_MAC_RESOURCES/LICENSES"

xattr -cr "$KD_MAC_APP"
find "$KD_MAC_APP" -type f -perm -111 -exec codesign --force --sign - {} \; >/dev/null 2>&1 || true
if [[ -n "${KD_CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --options runtime --timestamp --deep --sign "$KD_CODESIGN_IDENTITY" "$KD_MAC_APP"
  echo "Developer ID 签名：$KD_CODESIGN_IDENTITY。是否已 notarize 取决于外部发布流程。" > "$KD_MAC_DIST_ROOT/SIGNING_STATUS.txt"
else
  codesign --force --deep --sign - "$KD_MAC_APP"
  echo "仅使用 ad-hoc 本地签名；未 notarize。首次打开可能需要在 Finder 中右键选择“打开”。" > "$KD_MAC_DIST_ROOT/SIGNING_STATUS.txt"
fi
codesign --verify --deep --strict "$KD_MAC_APP"
plutil -lint "$KD_MAC_CONTENTS/Info.plist"

"$KD_MAC_ROOT/packaging/macos/smoke_test.sh" "$KD_MAC_APP"

for forbidden_path in "${HOME:?无法确定构建用户目录}" "$KD_MAC_ROOT" "$KD_MAC_BUILD_ROOT"; do
  if grep -R -a -F "$forbidden_path" "$KD_MAC_APP" >/dev/null 2>&1; then
    echo "安装包中检测到开发者绝对路径，已拒绝交付：$forbidden_path" >&2
    grep -R -a -l -F "$forbidden_path" "$KD_MAC_APP" | head -20 >&2
    exit 1
  fi
done

KD_MAC_DMG_STAGE="$KD_MAC_BUILD_ROOT/dmg"
rm -rf "$KD_MAC_DMG_STAGE"
mkdir -p "$KD_MAC_DMG_STAGE"
ditto --norsrc --noextattr --noqtn --noacl "$KD_MAC_APP" "$KD_MAC_DMG_STAGE/KnowledgeDebt.app"
ln -s /Applications "$KD_MAC_DMG_STAGE/Applications"
hdiutil create \
  -volname "KnowledgeDebt $KD_MAC_VERSION" \
  -srcfolder "$KD_MAC_DMG_STAGE" \
  -ov \
  -format UDZO \
  "$KD_MAC_DIST_ROOT/KnowledgeDebt-$KD_MAC_VERSION-arm64.dmg" >/dev/null
hdiutil verify "$KD_MAC_DIST_ROOT/KnowledgeDebt-$KD_MAC_VERSION-arm64.dmg" >/dev/null
ditto -c -k --norsrc --noextattr --noqtn --keepParent \
  "$KD_MAC_APP" \
  "$KD_MAC_DIST_ROOT/KnowledgeDebt-$KD_MAC_VERSION-arm64.zip"
cp "$KD_MAC_CACHE_ROOT/$KD_MAC_FFMPEG_ARCHIVE" \
  "$KD_MAC_DIST_ROOT/KnowledgeDebt-FFmpeg-$KD_MAC_FFMPEG_VERSION-source.tar.xz"
cp "$KD_MAC_CACHE_ROOT/$KD_MAC_WHISPER_ARCHIVE" \
  "$KD_MAC_DIST_ROOT/KnowledgeDebt-whisper.cpp-$KD_MAC_WHISPER_TAG-source.tar.gz"

(
  cd "$KD_MAC_DIST_ROOT"
  shasum -a 256 \
    "KnowledgeDebt-$KD_MAC_VERSION-arm64.dmg" \
    "KnowledgeDebt-$KD_MAC_VERSION-arm64.zip" \
    "KnowledgeDebt-FFmpeg-$KD_MAC_FFMPEG_VERSION-source.tar.xz" \
    "KnowledgeDebt-whisper.cpp-$KD_MAC_WHISPER_TAG-source.tar.gz" \
    > SHA256SUMS.txt
)

echo "构建完成：$KD_MAC_DIST_ROOT"
