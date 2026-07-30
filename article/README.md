# Artigo — Proposta de Portfólio (PAC 8)

**Sistema Multiagente Inteligente de Cobrança com LLM, RAG e Machine Learning**
Gabriel Forster Rocha — Centro Universitário Católica de Santa Catarina.

Artigo no formato **SBC Reviews 2025**. O corpo tem 4 páginas (dentro do limite de 4–6); o
Apêndice (tabela comparativa de trabalhos relacionados + cronograma de desenvolvimento) e as
Referências ficam nas páginas 5–6 e não contam para o limite.

## Arquivos

| Arquivo | Descrição |
|---|---|
| `main.tex` | Documento principal do artigo |
| `refs.bib` | Referências (BibTeX, estilo `apalike-sol`) |
| `sbcreviews-2025.cls` | Classe da SBC Reviews (do template) |
| `sectsty.sty`, `aas_macros.sty` | Pacotes de apoio (do template) |
| `apalike-sol.bst` | Estilo bibliográfico (do template) |
| `sol.jpg` | Logo usado pela classe |
| `build.sh` | Script de compilação (contorna instalação local quebrada do TeX) |

## Compilação

A classe usa `fontspec`, portanto **requer XeLaTeX (ou LuaLaTeX)** — não use `pdflatex`.
A sequência é sempre `xelatex → bibtex → xelatex → xelatex` (o `bibtex` no meio é o passo que
gera as referências; pular ele deixa a bibliografia vazia).

### Opção 1 — `./build.sh` (recomendado nesta máquina)

```sh
cd article
./build.sh
```

A instalação local de TeX Live está **incompleta** (falta `/var/lib/texmf`: sem formats, sem
font maps, sem `ls-R`), então `xelatex main` direto falha com
`xdvipdfmx:fatal: Cannot proceed without .vf or "physical" font`. O `build.sh` contorna isso
sem precisar de root: faz *live-scan* dos diretórios texmf e, **na primeira execução de um
clone novo**, gera o formato `xelatex` e os mapas de fonte sob `~/.texlive2023` (leva ~1 min;
execuções seguintes pulam essa etapa). Esse estado fica fora do repositório — por isso o script
o reconstrói sozinho quando ausente.

### Opção 2 — TeX reparado (precisa de root) ou Overleaf

Com o TeX Live reparado (`sudo apt-get install --reinstall texlive-base texlive-binaries &&
sudo fmtutil-sys --all && sudo updmap-sys`) o workaround é desnecessário e basta:

```sh
latexmk -xelatex -bibtex main.tex
```

Alternativa sem instalar nada: **Overleaf** com *Menu → Compiler → XeLaTeX*.

O resultado é sempre `main.pdf` (ignorado pelo Git).

## Citações

`apalike-sol.bst` gera entradas no formato *apalike* clássico. O `main.tex` coloca o `natbib`
em **modo numérico** (`\setcitestyle{numbers,square,comma}`), resultando em citações `[n]`
consistentes (ex.: `Wang et al. [18]`). Não altere isso sem trocar também o estilo `.bst`.
