#!/bin/bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：$0 /path/to/KnowledgeDebt.app" >&2
  exit 2
fi

KD_SMOKE_APP="$(cd "$1" && pwd)"
KD_SMOKE_RESOURCES="$KD_SMOKE_APP/Contents/Resources"
KD_SMOKE_TEMP="$(mktemp -d "${TMPDIR:-/tmp}/knowledgedebt-native-smoke.XXXXXX")"
KD_SMOKE_API_PORT=18124
KD_SMOKE_WEB_PORT=18125
KD_SMOKE_API_PID=""
KD_SMOKE_WEB_PID=""

cleanup() {
  for pid in "$KD_SMOKE_WEB_PID" "$KD_SMOKE_API_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  rm -rf "$KD_SMOKE_TEMP"
}
trap cleanup EXIT INT TERM

for binary in \
  "$KD_SMOKE_RESOURCES/backend/knowledgedebt-api" \
  "$KD_SMOKE_RESOURCES/runtime/node/bin/node" \
  "$KD_SMOKE_RESOURCES/runtime/ffmpeg/bin/ffmpeg" \
  "$KD_SMOKE_RESOURCES/runtime/ffmpeg/bin/ffprobe" \
  "$KD_SMOKE_RESOURCES/runtime/whisper/bin/whisper-cli"; do
  [[ -x "$binary" ]] || { echo "缺少可执行组件：$binary" >&2; exit 1; }
  if otool -L "$binary" | tail -n +2 | grep -E '/Users/|/opt/homebrew|/usr/local' >/dev/null; then
    echo "组件仍依赖开发机路径：$binary" >&2
    otool -L "$binary" >&2
    exit 1
  fi
done

for document in \
  "$KD_SMOKE_RESOURCES/DOCUMENTATION/DEFAULT_CONFIGURATION.env.example" \
  "$KD_SMOKE_RESOURCES/DOCUMENTATION/README.zh-CN.md" \
  "$KD_SMOKE_RESOURCES/DOCUMENTATION/macOS-安装与数据管理.md" \
  "$KD_SMOKE_RESOURCES/DOCUMENTATION/Provider-能力矩阵.md" \
  "$KD_SMOKE_RESOURCES/DOCUMENTATION/v0.2-发布与验收.md"; do
  [[ -s "$document" ]] || { echo "缺少应用内中文文档：$document" >&2; exit 1; }
done

while IFS= read -r -d '' component; do
  if file -b "$component" | grep -q 'Mach-O'; then
    if otool -L "$component" | tail -n +2 | grep -E '/Users/|/opt/homebrew|/usr/local' >/dev/null; then
      echo "应用内组件仍依赖开发机路径：$component" >&2
      otool -L "$component" >&2
      exit 1
    fi
  fi
done < <(find "$KD_SMOKE_APP" -type f -print0)

"$KD_SMOKE_RESOURCES/runtime/node/bin/node" --version | grep '^v24\.'
"$KD_SMOKE_RESOURCES/runtime/ffmpeg/bin/ffmpeg" -version | head -1
KD_SMOKE_WHISPER_VERSION="$("$KD_SMOKE_RESOURCES/runtime/whisper/bin/whisper-cli" --version 2>&1)"
printf '%s' "$KD_SMOKE_WHISPER_VERSION" | grep -q 'whisper.cpp version: 1.9.3'

KNOWLEDGEDEBT_DATA_DIR="$KD_SMOKE_TEMP/data" \
KNOWLEDGEDEBT_API_PORT="$KD_SMOKE_API_PORT" \
KNOWLEDGEDEBT_APP_VERSION="0.2.0" \
KNOWLEDGEDEBT_FFMPEG_PATH="$KD_SMOKE_RESOURCES/runtime/ffmpeg/bin/ffmpeg" \
KNOWLEDGEDEBT_LOCAL_ASR_BINARY="$KD_SMOKE_RESOURCES/runtime/whisper/bin/whisper-cli" \
KNOWLEDGEDEBT_LOCAL_ASR_MODEL_DIR="$KD_SMOKE_TEMP/data/models" \
"$KD_SMOKE_RESOURCES/backend/knowledgedebt-api" >"$KD_SMOKE_TEMP/backend.log" 2>&1 &
KD_SMOKE_API_PID=$!

for _ in {1..120}; do
  curl -fsS "http://127.0.0.1:$KD_SMOKE_API_PORT/health" >/dev/null 2>&1 && break
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$KD_SMOKE_API_PORT/health" | grep '"status":"ok"'
KD_SMOKE_PROVIDER_SETTINGS="$(curl -fsS "http://127.0.0.1:$KD_SMOKE_API_PORT/settings/provider")"
printf '%s' "$KD_SMOKE_PROVIDER_SETTINGS" | grep -q '"binary_ready":true'
printf '%s' "$KD_SMOKE_PROVIDER_SETTINGS" | grep -q '"id":"medium-q5_0"'

KD_SMOKE_WAV="$KD_SMOKE_TEMP/native-smoke.wav"
export KD_SMOKE_WAV
"$KD_SMOKE_RESOURCES/runtime/node/bin/node" -e '
const fs = require("fs");
const rate = 16000;
const samples = rate;
const dataBytes = samples * 2;
const output = Buffer.alloc(44 + dataBytes);
output.write("RIFF", 0);
output.writeUInt32LE(36 + dataBytes, 4);
output.write("WAVEfmt ", 8);
output.writeUInt32LE(16, 16);
output.writeUInt16LE(1, 20);
output.writeUInt16LE(1, 22);
output.writeUInt32LE(rate, 24);
output.writeUInt32LE(rate * 2, 28);
output.writeUInt16LE(2, 32);
output.writeUInt16LE(16, 34);
output.write("data", 36);
output.writeUInt32LE(dataBytes, 40);
for (let index = 0; index < samples; index += 1) {
  output.writeInt16LE(Math.round(Math.sin(index * Math.PI * 2 * 440 / rate) * 3000), 44 + index * 2);
}
fs.writeFileSync(process.env.KD_SMOKE_WAV, output);
'
KD_SMOKE_COURSE_JSON="$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"name":"原生安装冒烟课程","semester":"2026 秋"}' \
  "http://127.0.0.1:$KD_SMOKE_API_PORT/courses")"
KD_SMOKE_COURSE_ID="$(printf '%s' "$KD_SMOKE_COURSE_JSON" | \
  "$KD_SMOKE_RESOURCES/runtime/node/bin/node" -e 'let s=""; process.stdin.on("data", d => s += d).on("end", () => process.stdout.write(JSON.parse(s).id))')"
KD_SMOKE_SESSION_JSON="$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"title":"原生安装录音验收"}' \
  "http://127.0.0.1:$KD_SMOKE_API_PORT/courses/$KD_SMOKE_COURSE_ID/sessions")"
KD_SMOKE_SESSION_ID="$(printf '%s' "$KD_SMOKE_SESSION_JSON" | \
  "$KD_SMOKE_RESOURCES/runtime/node/bin/node" -e 'let s=""; process.stdin.on("data", d => s += d).on("end", () => process.stdout.write(JSON.parse(s).id))')"
curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"recording_id":"native-smoke-recording","mime_type":"audio/wav","filename":"native-smoke.wav","auto_transcribe":false}' \
  "http://127.0.0.1:$KD_SMOKE_API_PORT/sessions/$KD_SMOKE_SESSION_ID/recordings" >/dev/null
KD_SMOKE_CHUNK_SHA="$(shasum -a 256 "$KD_SMOKE_WAV" | awk '{print $1}')"
curl -fsS -X PUT \
  -H "X-Chunk-SHA256: $KD_SMOKE_CHUNK_SHA" \
  -H 'X-Recording-Stream-ID: native-smoke-stream' \
  -H 'X-Recording-Stream-Index: 0' \
  -F "file=@$KD_SMOKE_WAV;type=audio/wav" \
  "http://127.0.0.1:$KD_SMOKE_API_PORT/recordings/native-smoke-recording/chunks/0" >/dev/null
KD_SMOKE_FINAL_JSON="$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"last_sequence":0,"duration_seconds":1}' \
  "http://127.0.0.1:$KD_SMOKE_API_PORT/recordings/native-smoke-recording/finalize")"
printf '%s' "$KD_SMOKE_FINAL_JSON" | grep -q '"mime_type":"audio/flac"'
printf '%s' "$KD_SMOKE_FINAL_JSON" | grep -q '"status":"completed"'
find "$KD_SMOKE_TEMP/data/recording-chunks" -name '*.chunk' -type f -size +0 | grep -q .
KD_SMOKE_FINAL_AUDIO="$(find "$KD_SMOKE_TEMP/data/resources" -name '*.flac' -type f -size +0 | head -1)"
[[ -n "$KD_SMOKE_FINAL_AUDIO" ]]
"$KD_SMOKE_RESOURCES/runtime/ffmpeg/bin/ffprobe" -v error -select_streams a:0 \
  -show_entries stream=codec_name -of default=nw=1 "$KD_SMOKE_FINAL_AUDIO" | grep -q 'codec_name=flac'

(
  cd "$KD_SMOKE_RESOURCES/web"
  HOSTNAME=127.0.0.1 \
  PORT="$KD_SMOKE_WEB_PORT" \
  NODE_ENV=production \
  KNOWLEDGEDEBT_API_URL="http://127.0.0.1:$KD_SMOKE_API_PORT" \
  "$KD_SMOKE_RESOURCES/runtime/node/bin/node" server.js
) >"$KD_SMOKE_TEMP/web.log" 2>&1 &
KD_SMOKE_WEB_PID=$!

for _ in {1..120}; do
  curl -fsS "http://127.0.0.1:$KD_SMOKE_WEB_PORT" >/dev/null 2>&1 && break
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$KD_SMOKE_WEB_PORT" | grep -q 'KnowledgeDebt'
curl -fsS "http://127.0.0.1:$KD_SMOKE_WEB_PORT/api/backend/health" | grep '"status":"ok"'

[[ -s "$KD_SMOKE_TEMP/data/knowledgedebt.sqlite3" ]]
echo "原生应用运行时冒烟测试通过"
