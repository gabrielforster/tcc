# Artigo — Proposta de Portfólio (PAC 8)

**Sistema Multiagente Inteligente de Cobrança com LLM, RAG e Machine Learning**
Gabriel Forster Rocha · Orientador: Andrei Carniel — Centro Universitário Católica de Santa Catarina.

Artigo no formato **SBC Reviews 2025**, ~5 páginas (a seção *Apêndice* não conta para o
limite de 4–6 páginas). O Apêndice reúne a tabela comparativa de trabalhos relacionados e o
cronograma de desenvolvimento em semanas (jun.–dez. de 2026).

## Arquivos

| Arquivo | Descrição |
|---|---|
| `main.tex` | Documento principal do artigo |
| `refs.bib` | Referências (BibTeX, estilo `apalike-sol`) |
| `sbcreviews-2025.cls` | Classe da SBC Reviews (do template) |
| `sectsty.sty`, `aas_macros.sty` | Pacotes de apoio (do template) |
| `apalike-sol.bst` | Estilo bibliográfico (do template) |
| `sol.jpg` | Logo usado pela classe |

## Compilação

A classe usa `fontspec`, portanto **requer XeLaTeX (ou LuaLaTeX)** — não use `pdflatex`.

```sh
xelatex main
bibtex  main
xelatex main
xelatex main
```

Ou, de forma equivalente:

```sh
latexmk -xelatex -bibtex main.tex
```

O resultado é `main.pdf`. Recomenda-se compilar no Overleaf (selecionar **XeLaTeX** em
*Menu → Compiler*) caso o ambiente local não tenha as fontes necessárias.

## Citações

`apalike-sol.bst` gera entradas no formato *apalike* clássico. O `main.tex` coloca o `natbib`
em **modo numérico** (`\setcitestyle{numbers,square,comma}`), resultando em citações `[n]`
consistentes (ex.: `Wang et al. [18]`). Não altere isso sem trocar também o estilo `.bst`.
