# Pitch — Sistema Multiagente Inteligente de Cobrança

Apresentação de 3–5 min (banca + sala de aula), em **reveal.js**, com diagramas
desenhados à mão (SVG). Tudo é **offline**: reveal.js e fontes estão embutidos em
`vendor/` e `assets/fonts/` — não precisa de internet para apresentar.

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

Ou via linha de comando (Chrome headless):

```sh
google-chrome --headless --no-pdf-header-footer \
  --print-to-pdf=pitch.pdf "index.html?print-pdf"
```

## Editar

O `index.html` é estático e legível — edite o texto diretamente. Os diagramas são
SVG inline no próprio arquivo. O tema (cores, tipografia) está em `css/theme.css`.

- Cores e fontes: variáveis CSS no topo de `css/theme.css` (`--green`, `--paper`, …).
- Tipografia: Bricolage Grotesque (títulos) + IBM Plex Sans/Mono (corpo/rótulos).

## Fonte dos dados de mercado

Serasa Experian — *Indicador de Inadimplência das Empresas*, dezembro/2025
(R$ 213 bi em dívidas; 8,9 mi de empresas; 96% micro e pequenas; serviços 55% /
comércio 33%).
