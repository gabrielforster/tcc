---
title: "Cronograma de Entregas"
subtitle: "Sistema Multiagente Inteligente de Cobrança com LLM, RAG e Machine Learning"
author: "Gabriel Forster Rocha"
date: "Jul. a dez. de 2026 — Engenharia de Software, Católica de Santa Catarina"
lang: pt-BR
geometry: margin=2.2cm
fontsize: 10pt
colorlinks: true
---

## 1. Cronograma de entregas

Cada encontro tem **uma entrega pequena, verificável e acumulativa** — código rodando, dado
gerado, ou seção de texto escrita. A coluna *Artefato* é o que se mostra no encontro; a coluna
*Texto* é o que avança no artigo, para que a redação não fique concentrada no fim.

### Bloco I — Fundação: requisitos e dados (30/07 a 27/08)

| # | Data | Entrega | Artefato mostrado | Texto |
|:--|:-----|:----------------------------------------|:------------------|:--------------|
| 1 | **30/07** | **Definições.** Escopo fechado, este cronograma, repositório organizado (`article/`, `docs/`, RFC compilando) | RFC em PDF + cronograma aprovado | Proposta de portfólio revisada |
| 2 | 06/08 | **Requisitos e domínio.** Fluxo atual de cobrança mapeado, 10 RF + 6 RNF consolidados, restrições legais (CDC, LGPD, PROCON: horário, frequência, consentimento de gravação) e baselines de comparação definidos | Documento de requisitos + tabela de restrições legais | Seção de metodologia (etapa i) |
| 3 | 13/08 | **Dados brutos.** Fontes identificadas (cadastro, faturas, histórico de atraso, acordos, resultado de cobranças), extração do ERP, anonimização LGPD, dicionário de dados. Meta: ≥ 5.000 documentos históricos | Dataset anonimizado + dicionário de dados | Descrição da base de dados |
| 4 | 20/08 | **EDA e limpeza.** Estatísticas descritivas, distribuição do alvo, desbalanceamento medido, missings/outliers tratados, gráficos (atraso, inadimplência por segmento, sazonalidade, heatmap de correlação) | Notebook de EDA + 4 gráficos | Análise exploratória |
| 5 | 27/08 | **Features e splits.** *Feature engineering* (dias médios de atraso, taxa histórica, frequência recente, valor relativo, tendência de pagamento), encoding, escala, split 70/15/15 estratificado **respeitando ordem temporal** (sem vazamento) | Pipeline de features versionado + 3 conjuntos | Justificativa das features |

### Bloco II — Módulo preditivo (03/09 a 17/09)

| # | Data | Entrega | Artefato mostrado | Texto |
|:--|:-----|:----------------------------------------|:------------------|:--------------|
| 6 | 03/09 | **Baseline.** Regressão logística treinada na Tarefa 1 (propensão ao pagamento), métricas completas (acurácia, precisão, revocação, F1, AUC-ROC) + matriz de confusão | Tabela de métricas do baseline | Baseline preditivo |
| 7 | 10/09 | **Modelos fortes, Tarefa 1.** Random Forest e XGBoost/LightGBM com validação cruzada (k=5), *tuning* de hiperparâmetros e tratamento de desbalanceamento (SMOTE vs. *class weights*); modelo campeão escolhido | Comparativo de algoritmos + curvas ROC/PR | Resultados da Tarefa 1 |
| 8 | 17/09 | **Tarefa 2 + API.** Modelo de previsão de inadimplência futura (documentos não vencidos) treinado e avaliado, interpretabilidade (*feature importance* + SHAP), modelo serializado e exposto por **API de inferência** que devolve o *ranking* de prioridade | `POST /score` respondendo + gráfico SHAP | Resultados da Tarefa 2 e interpretabilidade |

### Bloco III — Arquitetura e módulo responsivo (24/09 a 15/10)

| # | Data | Entrega | Artefato mostrado | Texto |
|:--|:-----|:----------------------------------------|:------------------|:--------------|
| 9 | 24/09 | **Arquitetura multiagente.** Quatro camadas definidas, papéis dos três agentes, contrato de eventos, fila de mensagens e regra de prioridade (responsivo > proativo); esqueleto do **orquestrador central** rodando com agentes *stub* | Diagrama + orquestrador roteando evento de ponta a ponta | Seção de arquitetura |
| 10 | 01/10 | **RAG.** Ingestão (PDF/DOCX/CSV/TXT) de políticas, *scripts* de negociação, tabelas de parcelamento e FAQ; *chunking* (500–1000 tokens, *overlap* 50–100), *embeddings*, indexação vetorial e *retrieval* top-k avaliado por precisão/revocação | Consulta ao índice retornando trechos corretos | Pipeline RAG |
| 11 | 08/10 | **Agente responsivo (texto).** *System prompt*, classificação de intenção (consulta, 2ª via, negociação, contestação, confirmação de pagamento), resposta ancorada no RAG, critérios de escalação para humano | Conversa completa por texto, latência < 5 s medida | Módulo responsivo |
| 12 | 15/10 | **Canal + áudio.** Integração com WhatsApp Business API e transcrição de áudio (OGG/OPUS → STT), tratamento de erro (áudio inaudível, curto, *timeout*) | Áudio de WhatsApp respondido corretamente | Integração de voz (offline) |

### Bloco IV — Módulo ativo e telefonia (22/10 a 05/11)

| # | Data | Entrega | Artefato mostrado | Texto |
|:--|:-----|:----------------------------------------|:------------------|:--------------|
| 13 | 22/10 | **Detecção + regras.** *Job* periódico de documentos vencidos, consumo do *ranking* preditivo para cobrança preventiva, motor de regras (horário permitido, frequência máxima, canal, *cooldown*, limite de tentativas) | Fila de cobrança populada com prioridade preditiva | Módulo ativo (parte 1) |
| 14 | 29/10 | **Abordagem multicanal por texto.** *Templates* por estágio de atraso (1–7, 8–30, 31–60, 60+ dias) personalizados por LLM, envio pelo WhatsApp, *tracking* de status (enviada/entregue/lida/respondida), respeito imediato a *opt-out*, transferência ao agente responsivo quando o cliente responde | Escalonamento progressivo executando em ambiente de teste | Módulo ativo (parte 2) |
| 15 | 05/11 | **Chamada telefônica automatizada.** Número outbound, *webhooks*, fluxo TTS de abertura → STT em tempo real → resposta → encerramento, aviso de gravação e registro completo da chamada | Chamada real gravada e transcrita | Integração de voz (tempo real) |

### Bloco V — Avaliação e fechamento (12/11 a 03/12)

| # | Data | Entrega | Artefato mostrado | Texto |
|:--|:-----|:----------------------------------------|:------------------|:--------------|
| 16 | 12/11 | **Testes e início do experimento.** Testes unitários (parser, *features*, motor de regras, intenção, *templates*) com ≥ 80% das funções críticas cobertas, testes de integração dos três fluxos e dos serviços externos; **experimento controlado iniciado** contra os dois baselines (manual e semiautomatizado sem ML) | Suíte de testes verde + experimento coletando dados | Protocolo experimental |
| 17 | 19/11 | **Resultados consolidados.** Métricas preditivas finais vs. baseline, qualidade do RAG (*retrieval* + fidelidade/completude/alucinação das respostas), indicadores operacionais (taxa de recuperação, tempo médio de resolução, custo por contato, ≥ 100 interações concorrentes), análise estatística | Tabelas e gráficos finais de resultados | Seção de resultados e discussão |
| 18 | **26/11** | **Revisão final.** Artigo completo revisado (resumo/abstract, referências, tabelas, figuras), limitações e trabalhos futuros, README de reprodução; ajuste dos pontos apontados pelo orientador | Artigo em PDF, versão candidata | Texto integral fechado |
| 19 | **03/12** | **Trabalho finalizado.** Versão final entregue, repositório com *tag* de entrega, apresentação/defesa preparada e ensaiada | PDF final + *slides* + demonstração | — |

---

## 2. Relação com o cronograma do RFC

O RFC (Tabela 3) previa 24 semanas em 8 fases; este cronograma distribui as mesmas 8 fases nos
19 encontros, sobrepondo blocos que podem correr em paralelo (arquitetura durante o fim do módulo
preditivo; voz sobre o módulo ativo) e reservando as três últimas semanas para avaliação, redação
e defesa.

| Fase do RFC | Encontros |
|---|---|
| 1 — Requisitos e análise do domínio | 30/07, 06/08 |
| 2 — Coleta e preparação de dados | 13/08, 20/08, 27/08 |
| 3 — Módulo preditivo de ML | 03/09, 10/09, 17/09 |
| 4 — Arquitetura multiagente | 24/09 |
| 5 — Módulo responsivo (RAG + LLM) | 01/10, 08/10, 15/10 |
| 6 — Módulo ativo/proativo | 22/10, 29/10 |
| 7 — Integração de voz | 15/10 (STT offline), 05/11 (telefonia) |
| 8 — Testes, experimentos e avaliação | 12/11, 19/11 |
| Redação final e defesa | 26/11, 03/12 |

---

## 3. Riscos e plano de contingência

| Risco | Impacto | Contingência |
|---|---|---|
| Volume ou qualidade insuficiente dos dados históricos | Bloqueia as entregas 6–8 | Antecipar a extração (13/08) e, se necessário, usar base pública de *credit scoring* para validar o pipeline, mantendo a base real apenas para o modelo final |
| Aprovação de *templates* na Meta / conta de telefonia | Atrasa 29/10 e 05/11 | Iniciar cadastro já em 15/10; ambiente *sandbox* do provedor como plano B da demonstração |
| Latência das chamadas com STT em tempo real | Compromete a entrega 15 | Reduzir o escopo da chamada para roteiro guiado (menos turnos livres) e registrar a limitação |
| Experimento operacional com amostra pequena | Enfraquece a entrega 17 | Relatar tamanho de efeito e intervalo de confiança; complementar com métricas técnicas e comparação de custo por contato |
| Custo de APIs de LLM, STT/TTS e telefonia | Afeta 08/10 em diante | Cache de respostas, modelo menor para tarefas simples, Whisper local como alternativa |

**Escopo mínimo garantido** (se algo precisar cair): os módulos responsivo e preditivo mais o
fluxo proativo por texto são obrigatórios; a chamada telefônica em tempo real é a primeira
candidata a virar prova de conceito reduzida, com a limitação documentada no artigo.

---

<!--
Gerar o PDF (fontes DejaVu porque Latin Modern nao tem os glifos >= e <):

  pandoc cronograma.md -o cronograma.pdf --pdf-engine=xelatex \
    -V mainfont="DejaVu Serif" -V sansfont="DejaVu Sans" -V monofont="DejaVu Sans Mono" \
    -V fontsize=9pt -V geometry:landscape -V geometry:margin=1.8cm

Se o TeX Live local estiver quebrado, exportar antes as variaveis de article/build.sh
(TEXMFVAR / TEXMFCONFIG / TEXMFDIST / TEXMF e TEXMFDBS="").
-->
