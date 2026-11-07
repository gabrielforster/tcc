# Pitch — Sistema Multiagente Inteligente de Cobrança

Apresentação de 3–5 min (banca + sala de aula), em **reveal.js**, com diagramas
desenhados à mão (SVG). Tudo é **offline**: reveal.js e fontes estão embutidos em
`vendor/` e `assets/fonts/` — não precisa de internet para apresentar.

## Defesa (03/12/2026)

A apresentação de defesa fica ao lado do pitch, no mesmo formato e tema:

| Arquivo | O que é |
|---|---|
| `defesa.html` | Deck de 19 slides (~16 min de fala + ~3 min de demonstração). Abre como o `index.html`. |
| `roteiro-defesa.md` | Roteiro falado com tempos por slide e as perguntas prováveis da banca com respostas. |
| `css/defesa.css` | Estilos que a defesa usa além do `theme.css` (tabelas, capturas de tela). |
| `assets/img/` | Figuras copiadas de `docs/images/` e `article/figuras/`, para o deck continuar offline. |

O roteiro da demonstração ao vivo está em [`docs/demo-script.md`](../docs/demo-script.md), e o
checklist de entrega em [`docs/delivery-checklist.md`](../docs/delivery-checklist.md).

Um valor do deck ainda pode mudar — a duração mediana de uma resposta falada na chamada de
voz. Ele está numa única constante (`DEFESA.vozFala`, no fim de `defesa.html`) e nos dois
pontos marcados com `VOZ_FALA` no roteiro.

Para exportar o PDF: `./export-pdf.sh defesa.html` (uma página por slide, com todos os
fragmentos visíveis). Veja "Exportar PDF" abaixo.

## Como apresentar

Abra `index.html` no navegador (basta dar duplo-clique). Se o seu navegador for
restritivo com `file://`, sirva a pasta localmente:

```sh
cd pitch
python3 -m http.server 8000
# abra http://localhost:8000
```

### Atalhos (reveal.js)

| Tecla | Ação |
|-------|------|
| `→` / `Espaço` | próximo passo (avança também as partes do diagrama da arquitetura) |
| `←` | voltar |
| `S` | **modo apresentador** — abre janela com o roteiro falado (notas) e cronômetro |
| `F` | tela cheia |
| `O` | visão geral de todos os slides |
| `B` | tela preta (pausa) |

> O **roteiro falado** em PT-BR está nas notas de cada slide (`S`). A soma das
> notas dá ~4 minutos.

## Estrutura (9 slides)

1. Capa
2. Contexto — inadimplência recorde (dados Serasa Experian, dez/2025)
3. O problema — cobrança ainda manual
4. Proposta — arquitetura multiagente *(diagrama-herói)*
5. Como funciona · Responsivo *(fluxo)*
6. Como funciona · Proativo + Preditivo *(fluxo)*
7. Inovação
8. Necessidade de mercado
9. Pergunta de pesquisa + métricas

## Exportar PDF

1. Abra `index.html?print-pdf` no Chrome.
2. `Ctrl/Cmd + P` → **Salvar como PDF**.
3. Layout **Paisagem**, margens **Nenhuma**, **Gráficos de plano de fundo** ligado.

Ou pela linha de comando, com Chromium/Chrome e `uv` instalados:

```sh
./export-pdf.sh index.html pitch.pdf
./export-pdf.sh defesa.html defesa.pdf
```

O script fotografa cada slide em 1280×720 com todos os fragmentos visíveis e junta as
imagens num PDF. O `--print-to-pdf` headless sobre `?print-pdf` não serve com o Chromium atual
(07/11/2026): ele imprime antes de o reveal.js montar o layout de impressão, e sai um PDF vazio
ou paginado como página web.

## Editar

O `index.html` é estático e legível — edite o texto diretamente. Os diagramas são
SVG inline no próprio arquivo. O tema (cores, tipografia) está em `css/theme.css`.

- Cores e fontes: variáveis CSS no topo de `css/theme.css` (`--green`, `--paper`, …).
- Tipografia: Bricolage Grotesque (títulos) + IBM Plex Sans/Mono (corpo/rótulos).

## Fonte dos dados de mercado

Serasa Experian — *Indicador de Inadimplência das Empresas*, dezembro/2025
(R$ 213 bi em dívidas; 8,9 mi de empresas; 96% micro e pequenas; serviços 55% /
comércio 33%).
