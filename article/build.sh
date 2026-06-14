#!/usr/bin/env bash
# Gera main.pdf a partir de main.tex.
#
# Este projeto roda em um TeX Live cuja instalação está incompleta
# (falta /var/lib/texmf: sem formats, font maps e ls-R). Este script
# contorna isso forçando o kpathsea a varrer o disco em vez de usar ls-R.
#
# Se você REPARAR o TeX Live (ver README.md), pode ignorar este script e
# usar simplesmente:  latexmk -xelatex -bibtex main.tex
set -e
cd "$(dirname "$0")"

# Workaround para a instalação quebrada: live-scan dos diretórios texmf.
export TEXMFVAR="$HOME/.texlive2023/texmf-var"
export TEXMFCONFIG="$HOME/.texlive2023/texmf-config"
export TEXMFHOME="$HOME/texmf"
export TEXMFDIST="/usr/share/texlive/texmf-dist"
export TEXMFMAIN="/usr/share/texmf"
export TEXMFLOCAL="/usr/local/share/texmf"
export TEXMF="{$TEXMFVAR,$TEXMFCONFIG,$TEXMFHOME,$TEXMFLOCAL,$TEXMFDIST,$TEXMFMAIN}"
export TEXMFDBS=""

echo ">> xelatex (passo 1/4)"; xelatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
echo ">> bibtex  (passo 2/4)"; bibtex main >/dev/null
echo ">> xelatex (passo 3/4)"; xelatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
echo ">> xelatex (passo 4/4)"; xelatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null

# limpa artefatos intermediários, mantém o PDF
rm -f main.aux main.bbl main.blg main.out main.log
echo ">> OK: $(pwd)/main.pdf gerado."
