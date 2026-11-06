#!/usr/bin/env bash
# Turn the raw Playwright recordings into the pack's videos.
# usage: bash video.sh PACK_DIR   (after capture.py)
set -euo pipefail
PACK="$1"; V="$PACK/video"; RAW="$V/raw"; AUD="$PACK/mock/voice"
T=$(mktemp -d)
FONT=/usr/share/fonts/noto/NotoSans-Bold.ttf
enc=(-c:v libx264 -pix_fmt yuv420p -crf 20 -preset slow -movflags +faststart)

# 1. console walkthrough: drop the first blank second
ffmpeg -loglevel error -y -ss 0.8 -i "$(ls "$RAW"/console/*.webm | head -1)" "${enc[@]}" "$V/01-console.mp4"

# 2. voice call: place every clip at its logged cue, cut where the page cut it (barge-in)
python3 - "$RAW/voz-audio.json" "$AUD" "$T/mix.sh" <<'EOF'
import json, sys
log, aud, out = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
ins, chains = [], []
for i, e in enumerate(log["events"]):
    ins += ["-i", f"{aud}/{e['audio']}"]
    delay = int(log["offset_ms"] + e["at"])
    dur = e["dur"] / 1000
    chains.append(f"[{i}]atrim=0:{dur:.3f},afade=t=out:st={max(dur - 0.08, 0):.3f}:d=0.08,"
                  f"aresample=48000,adelay={delay}|{delay}[a{i}]")
mix = "".join(f"[a{i}]" for i in range(len(chains)))
graph = ";".join(chains) + f";{mix}amix=inputs={len(chains)}:normalize=0[aout]"
open(out, "w").write("ffmpeg -loglevel error -y " + " ".join(f"'{x}'" for x in ins)
                     + f" -filter_complex '{graph}' -map '[aout]' -ac 2 \"$1\"\n")
print("start", int(log["offset_ms"]))
EOF
bash "$T/mix.sh" "$T/voz.wav"
START=$(python3 -c "import json;print(max(json.load(open('$RAW/voz-audio.json'))['offset_ms']/1000-1.2,0))")
ffmpeg -loglevel error -y -i "$(ls "$RAW"/voz/*.webm | head -1)" -i "$T/voz.wav" -map 0:v -map 1:a \
  "${enc[@]}" -c:a aac -b:a 160k -shortest "$T/voz-full.mp4"
ffmpeg -loglevel error -y -ss "$START" -i "$T/voz-full.mp4" "${enc[@]}" -c:a aac -b:a 160k "$V/02-chamada-de-voz.mp4"

# 3. combined, with title cards (silent cards get a silent track so concat keeps audio)
card() {
  printf '%s' "$1" > "$T/t1.txt"; printf '%s' "$2" > "$T/t2.txt"
  ffmpeg -loglevel error -y -f lavfi -i "color=c=0x17423a:s=1280x800:d=3:r=25" -f lavfi -i anullsrc=r=48000:cl=stereo \
    -vf "drawtext=fontfile=$FONT:textfile=$T/t1.txt:fontcolor=white:fontsize=44:x=(w-tw)/2:y=(h-th)/2-30,drawtext=fontfile=$FONT:textfile=$T/t2.txt:fontcolor=0xbfe3d6:fontsize=25:x=(w-tw)/2:y=(h/2)+40" \
    -t 3 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$3"
}
card 'Prévia: voz e console interno' 'Sistema Multiagente de Cobrança · 06/11/2026' "$T/c0.mp4"
card '1. Chamada de voz em tempo real' 'áudio local (Piper) · transcrição, intenção e tempos medidos' "$T/c1.mp4"
card '2. Console interno' 'como os modelos classificam cada cliente · fila de escalação' "$T/c2.mp4"
ffmpeg -loglevel error -y -i "$V/01-console.mp4" -f lavfi -i anullsrc=r=48000:cl=stereo -map 0:v -map 1:a -c:v copy -c:a aac -shortest "$T/console-a.mp4"
N='scale=1280:800,fps=25,format=yuv420p,setsar=1'
ffmpeg -loglevel error -y -i "$T/c0.mp4" -i "$T/c1.mp4" -i "$V/02-chamada-de-voz.mp4" -i "$T/c2.mp4" -i "$T/console-a.mp4" \
  -filter_complex "[0:v]$N[v0];[1:v]$N[v1];[2:v]$N[v2];[3:v]$N[v3];[4:v]$N[v4];[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];[2:a]aresample=48000[a2];[3:a]aresample=48000[a3];[4:a]aresample=48000[a4];[v0][a0][v1][a1][v2][a2][v3][a3][v4][a4]concat=n=5:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" "${enc[@]}" -c:a aac -b:a 160k "$V/00-demo-voz-e-console.mp4"
rm -rf "$RAW" "$T"
