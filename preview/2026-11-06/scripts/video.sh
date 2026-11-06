#!/usr/bin/env bash
set -euo pipefail
cd "$1"
FONT=/usr/share/fonts/noto/NotoSans-Bold.ttf
T=/tmp/tcc-demo
if [ "$(ffprobe -v error -show_entries format=duration -of csv=p=0 02-opt-out.mp4 | cut -d. -f1)" -gt 12 ]; then
  ffmpeg -loglevel error -y -i 02-opt-out.mp4 -t 10 -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart $T/oo.mp4 && mv $T/oo.mp4 02-opt-out.mp4
fi
card() {
  printf '%s' "$1" > $T/t1.txt; printf '%s' "$2" > $T/t2.txt
  ffmpeg -loglevel error -y -f lavfi -i "color=c=0x17423a:s=1280x800:d=2.5:r=25" \
    -vf "drawtext=fontfile=$FONT:textfile=$T/t1.txt:fontcolor=white:fontsize=44:x=(w-tw)/2:y=(h-th)/2-30,drawtext=fontfile=$FONT:textfile=$T/t2.txt:fontcolor=0xbfe3d6:fontsize=26:x=(w-tw)/2:y=(h/2)+40" \
    -c:v libx264 -pix_fmt yuv420p "$3"
}
card 'Sistema Multiagente de Cobrança' 'Prévia do estado atual · 06/11/2026' $T/c0.mp4
card '1. Contato proativo e atendimento' 'mensagem semeada, dúvidas respondidas pelo RAG, contestação escalada' $T/c1.mp4
card '2. Opt-out' 'o pedido do cliente é registrado e respeitado' $T/c2.mp4
card '3. No celular' 'o mesmo widget, embutido em qualquer site' $T/c3.mp4
N='scale=1280:800:force_original_aspect_ratio=decrease,pad=1280:800:(ow-iw)/2:(oh-ih)/2:color=0xf1efe9,fps=25,format=yuv420p,setsar=1'
ffmpeg -loglevel error -y -i $T/c0.mp4 -i $T/c1.mp4 -i 01-conversa.mp4 -i $T/c2.mp4 -i 02-opt-out.mp4 -i $T/c3.mp4 -i 03-celular.mp4 \
  -filter_complex "[0]${N}[a];[1]${N}[b];[2]${N}[c];[3]${N}[d];[4]${N}[e];[5]${N}[f];[6]${N}[g];[a][b][c][d][e][f][g]concat=n=7:v=1:a=0[v]" \
  -map "[v]" -c:v libx264 -crf 20 -preset slow -movflags +faststart 00-demo-completo.mp4
rm -f 04-api.mp4; rm -rf raw
