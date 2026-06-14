#!/usr/bin/env bash
# Gera main.pdf a partir de main.tex (XeLaTeX + BibTeX).
#
# Este projeto roda em um TeX Live cuja instalação de SISTEMA está incompleta
# (falta /var/lib/texmf: sem formats, sem font maps, sem ls-R). Este script
# contorna isso de duas formas, SEM precisar de root:
#   1. Força o kpathsea a varrer o disco (TEXMFDBS="") em vez de usar ls-R.
#   2. Faz bootstrap (uma única vez) do formato xelatex e dos mapas de fonte
#      sob ~/.texlive2023, caso ainda não existam. É ISTO que faz um CLONE NOVO
#      compilar sem o erro 'xdvipdfmx:fatal: Cannot proceed without .vf or
#      "physical" font' — esse estado fica fora do repositório.
#
# Se você REPARAR o TeX Live (ver README.md) ou usar Overleaf, pode ignorar
# este script. Com o TeX reparado basta:  latexmk -xelatex -bibtex main.tex
set -e
cd "$(dirname "$0")"

# --- Workaround 1: live-scan dos diretórios texmf -----------------------------
export TEXMFVAR="$HOME/.texlive2023/texmf-var"
export TEXMFCONFIG="$HOME/.texlive2023/texmf-config"
export TEXMFHOME="$HOME/texmf"
export TEXMFDIST="/usr/share/texlive/texmf-dist"
export TEXMFMAIN="/usr/share/texmf"
export TEXMFLOCAL="/usr/local/share/texmf"
export TEXMF="{$TEXMFVAR,$TEXMFCONFIG,$TEXMFHOME,$TEXMFLOCAL,$TEXMFDIST,$TEXMFMAIN}"
export TEXMFDBS=""

# --- Workaround 2: bootstrap de formato + mapas (idempotente) -----------------
FMT="$TEXMFVAR/web2c/xetex/xelatex.fmt"
MAP="$TEXMFVAR/fonts/map/dvips/updmap/psfonts.map"

if [ ! -f "$FMT" ]; then
  echo ">> [setup 1/2] gerando formato xelatex (uma vez)..."
  mkdir -p "$TEXMFCONFIG/web2c"
  cat > "$TEXMFCONFIG/web2c/fmtutil.cnf" <<'CNF'
xetex     xetex     -            -etex xetex.ini
xelatex   xetex     language.dat -etex xelatex.ini
pdftex    pdftex    -            -etex pdftex.ini
pdflatex  pdftex    language.dat -etex pdflatex.ini
CNF
  fmtutil-user --byfmt xelatex >/tmp/build-fmtutil.log 2>&1 \
    || { echo "ERRO ao gerar o formato; veja /tmp/build-fmtutil.log"; exit 1; }
fi

if [ ! -f "$MAP" ]; then
  echo ">> [setup 2/2] gerando mapas de fonte (uma vez)..."
  mkdir -p "$TEXMFCONFIG/web2c"
  find "$TEXMFDIST/fonts/map/dvips" -name "*.map" -printf "Map %f\n" 2>/dev/null \
    | sort -u > "$TEXMFCONFIG/web2c/updmap.cfg"
  updmap-user >/tmp/build-updmap.log 2>&1 \
    || { echo "ERRO ao gerar os mapas; veja /tmp/build-updmap.log"; exit 1; }
fi

# --- Compilação: xelatex -> bibtex -> xelatex -> xelatex ----------------------
echo ">> xelatex (passo 1/4)"; xelatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
echo ">> bibtex  (passo 2/4)"; bibtex main >/dev/null
echo ">> xelatex (passo 3/4)"; xelatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
echo ">> xelatex (passo 4/4)"; xelatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null

# limpa artefatos intermediários, mantém o PDF
rm -f main.aux main.bbl main.blg main.out main.log
echo ">> OK: $(pwd)/main.pdf gerado."
