# Roteiro da defesa — Sistema Multiagente Inteligente de Cobrança

> Defesa de **03/12/2026**. Deck: `pitch/defesa.html` (19 slides). Tempo-alvo: **~15 min 45 s de
> fala + ~3 min de demonstração ao vivo ≈ 19 min**, dentro da janela de 15–20 min.
> O texto em *itálico entre colchetes* é instrução de palco, não para falar.
> A versão resumida deste roteiro está nas **notas de cada slide** (tecla `S` no navegador).
> A demonstração tem roteiro próprio, com comandos e plano B: [`docs/demo-script.md`](../docs/demo-script.md).

**Dicas de entrega:** todo número do deck vem de `docs/` ou do artigo — se a banca perguntar
"de onde vem isso?", a resposta é sempre um arquivo. Diga "simulação" e "dados sintéticos" em voz
alta quando forem o caso: a honestidade sobre o que *não* foi medido é parte do argumento.

## Valor que vem de uma remedição

Um valor depende de uma medição que pode ser refeita. No deck ele vem de **uma única constante** (`DEFESA.vozFala`, no
fim de `defesa.html`); aqui ele aparece só nos pontos marcados com `VOZ_FALA`. Ao trocar o número,
troque os três lugares. (Os 102 s da primeira medição são história — o problema achado — e não
mudam.)

| Chave | Valor atual | Fonte |
|---|---|---|
| `VOZ_FALA` — duração mediana de uma resposta falada na chamada | **11,7 s** (07/11/2026) | `docs/voice-call.md` |

---

## Slide 1 — Capa · ~25 s

*[Olhe para a banca antes de começar.]*

"Bom dia. Sou Gabriel Forster Rocha e vou apresentar o resultado do meu TCC: um **sistema
multiagente de cobrança**. Na proposta, eu descrevi o que pretendia construir. Hoje eu mostro o
que foi **construído**, o que foi **medido** e — tão importante quanto — o que **ficou em aberto**,
e por quê."

## Slide 2 — O problema · ~40 s

"O ponto de partida é o mesmo da proposta: inadimplência recorde — **213 bilhões de reais** em
dívidas de empresas e **8,9 milhões** de CNPJs inadimplentes — e uma cobrança ainda
majoritariamente manual, priorizada por intuição. Mas um detalhe pesou no projeto inteiro:
cobrança é atividade **regulada**. Horário permitido, frequência, *opt-out*, LGPD, a lei do
superendividamento. Uma automação que esqueça isso é um passivo, não um produto."

## Slide 3 — Pergunta de pesquisa · ~40 s

"A pergunta é esta. Vou adiantar a resposta, para que vocês julguem o resto contra ela: **sim,
mas de forma condicional**. A predição melhora a recuperação **na medida em que a capacidade de
contato é escassa**. E isso foi mostrado em **simulação**, não numa operação real, porque os
dados de uma operação real não estavam disponíveis."

## Slide 4 — Arquitetura · ~1 min 15 s

*[Avance com `→` revelando: orquestrador → agentes → canais e serviços → registro durável.]*

"No topo, o **orquestrador**: recebe eventos e decide quem age. Uma decisão de projeto: o
**opt-out é verificado antes de qualquer agente rodar** — a conformidade não depende de um agente
lembrar dela. *[→]* Três agentes: o **responsivo** classifica a intenção e responde com base em
documentos recuperados, ou escala para uma pessoa; o **proativo** decide quem contatar, quando e
com que mensagem, sob o motor de regras; o **preditivo** ordena a carteira e é servido por uma API.
*[→]* Os canais seguem um **contrato único** — chat e voz — e nenhum agente importa um canal: um
teste verifica isso. Reconhecimento e síntese de fala rodam **localmente**, e o LLM é acessado por
uma interface compatível com OpenAI, então trocar de modelo é configuração. *[→]* Por baixo, um
registro durável multi-tenant, com chave de pseudonimização derivada por cliente."

## Slide 5 — Dados · ~55 s

"A primeira descoberta do trabalho foi sobre dados, e ela moldou o resto. **Não existe base
pública** que ligue recebíveis aos contatos de cobrança feitos sobre eles, e o acesso ao ERP não
aconteceu. A decisão — que no repositório se chama **D1** — foi trabalhar com dados públicos e
sintéticos e tratar a comparação operacional como **limitação declarada**. O gerador sintético
verifica o protocolo de treino. A base **Bank Marketing**, com 41.188 contatos reais, é o único
comportamento humano real do trabalho. E a LGPD foi tratada por construção: identificadores
suprimidos, pseudônimo HMAC-SHA256 com chave por *tenant*."

## Slide 6 — Módulo preditivo · ~55 s

*[Aponte para a etiqueta "dados sintéticos".]*

"Duas tarefas: propensão a pagar e inadimplência futura. **Estes números vêm do gerador
sintético**: mostram que o protocolo de treino funciona, sem vazamento — não que o modelo prevê
pagamento real. O achado é metodológico: na tarefa 2, o campeão tem **acurácia menor** que prever
sempre a classe majoritária — 0,762 contra 0,836 — e é o único modelo útil: multiplica a precisão
média por 2,5 e encontra 71% dos inadimplentes. A meta de acurácia da proposta estava mal
formulada para um alvo desbalanceado; a seleção usa **precisão média**, com validação temporal e
*split* cronológico."

## Slide 7 — Estratégia de contato · ~50 s

"Este é o resultado sobre comportamento humano real. A taxa de resposta cai a cada tentativa: de
**13% na primeira para 5,5% a partir da sexta** — queda de 58%. Por isso o motor de regras limita
a **quatro tentativas, com 48 horas** de intervalo: os números vêm daqui. E a janela de horário é
avaliada no fuso de São Paulo — oito da manhã UTC são cinco da manhã aqui, horário ilegal de
contato. A ressalva: é telemarketing bancário, não cobrança."

## Slide 8 — Agente responsivo · ~50 s

"O agente responsivo acerta a intenção em **0,913** das 23 mensagens rotuladas e responde sempre
com citação; sem evidência, **escala para uma pessoa**, e um operador assume e resolve. Duas
expectativas registradas antes da medição falharam: o *embedder* neural **não superou** o TF-IDF,
e acrescentar o contexto da conversa por concatenação **piorou** a recuperação — de 0,889 para
0,778 — antes de uma versão com consultas intercaladas melhorar os acompanhamentos de 0,733 para
0,800. A lição: um *benchmark* que não expressa a falha não mede a correção."

## Slide 9 — Agente proativo e conformidade · ~50 s

"No proativo, a conformidade é uma **propriedade verificável**, não uma instrução no *prompt*. O
opt-out é checado no agendamento e de novo na entrega. Toda mensagem passa por um checador com
cinco verificações. E o checador achou um problema no meu próprio controle: um *template* por
faixa de atraso passou em 18 de 20 casos; as duas falhas eram SMS com uns 250 caracteres. A
mensagem é função da faixa **e** do canal. No SMS, o que sai primeiro é a saudação pelo nome,
porque as três divulgações obrigatórias não podem sair."

## Slide 10 — Mudança de escopo · ~50 s

"A principal mudança de escopo, e quero ser transparente sobre ela. A proposta previa WhatsApp
Business e telefonia real. As duas exigiam conta comercial, aprovação de *templates* e custo — e
**não acrescentavam nenhuma decisão nova** aos agentes. Troquei por um chat simulado e uma
chamada por WebRTC autohospedada, atrás do **mesmo contrato de canal**. O que se perde, e está no
artigo: 'entregue' e 'lida' vêm do navegador, não de uma operadora — então não há métrica de
alcance de campanha."

## Slide 11 — Voz, erro de reconhecimento · ~45 s

"Na voz, o achado é uma **assimetria**. A intenção é dita de várias formas na mesma frase e
sobrevive ao erro; um valor é dito uma vez só. A uma taxa de erro de palavras de 0,099 — uma
transcrição que se lê sem estranhamento — **um em cada sete valores falados já está errado**, e o
reconhecedor continua confiante. Por isso **todo valor ouvido é lido de volta** antes de virar
ação."

## Slide 12 — Chamada em tempo real · ~1 min

"A chamada funciona de ponta a ponta numa sala LiveKit real: **2,2 segundos** do fim da fala até a
primeira palavra da resposta. O Whisper é cerca de 60% disso; o agente, menos de 60 milissegundos.
Tudo é local, porque a resposta lê em voz alta a dívida de uma pessoa nomeada. A primeira medição
achou um problema que a tabela não mostra: o *template* lia trechos inteiros, e cada resposta
falada durava em mediana **102 segundos**. A correção não foi no canal — o contrato proíbe o canal
de decidir que parte de uma política o cliente ouve. A chamada **declara** quanto cabe: **43
palavras**, de uma meta de 20 segundos vezes a velocidade mais lenta medida do sintetizador, 2,15
palavras por segundo. O respondedor escolhe a seção do documento que melhor responde e a lê
literalmente, sem parafrasear. Medido de novo: mediana de **11,7 s** <!-- VOZ_FALA -->, máximo de
16,2, as 18 falas dentro da meta — e o chat continua idêntico. Limitação: a recuperação ainda pode
escolher o trecho errado. O que **não** foi verificado: uma pessoa falando pelo microfone do
navegador."

*[DEMONSTRAÇÃO AO VIVO — ~3 min. Siga `docs/demo-script.md`: chat semeado pelo agente proativo,
pergunta respondida com citação, escalação; depois a chamada. Se algo falhar, vá direto ao plano B
do roteiro e volte ao slide 13.]*

## Slide 13 — Experimento, figura · ~50 s

"Este é o resultado central. Como não havia operação real, o experimento é uma **simulação
declarada**: carteira sintética, quatro braços — manual, semiautomatizado, só a ordenação e o
sistema completo — sob as mesmas regras, com a resposta ao contato vinda da curva da Bank
Marketing, e 30 replicações pareadas por semente. *[Aponte o eixo horizontal.]* Aqui, quantos
contatos por dia a operação consegue fazer. Quando a capacidade é escassa, o sistema se separa;
quando há capacidade para todos, as curvas se encontram: a ordem deixa de importar."

## Slide 14 — Experimento, números · ~1 min 10 s

"Com 2 a 4 contatos por dia, o sistema recupera **4,8 pontos percentuais a mais** do valor em
risco em 30 dias do que o semiautomatizado, com intervalo de 3,9 a 5,8. Com 8 por dia, 1,3; com
16, nada. A ablação mostra que **o ganho vem da ordenação**: os lembretes preventivos acrescentam
no máximo 0,1 ponto. A automação sozinha reduz muito o custo: R$ 34,62 por mil recuperados numa
mesa manual, R$ 6,37 no semiautomatizado, R$ 1,94 com o sistema. Em 90 dias todos recuperam o
mesmo: o contato muda **quando** se paga, não **se** se paga. E a auditoria independente achou
**zero violações** em 1.500 execuções. Ressalva: as magnitudes pertencem ao simulador; afirmo a
direção e a dependência da capacidade."

## Slide 15 — Braço gerativo · ~1 min

"Eu não tinha chave de um provedor hospedado, então medi com um modelo de **3 bilhões de
parâmetros, local, em CPU** — o `qwen2.5:3b`. É um **piso**. Foi menos fiel que o *template* —
0,692 contra 1,000 — e lento demais para chat: 11 segundos no p95. Ofereceu 'até 12 vezes' para
um atraso de 45 dias, e a checagem de números pegou. Nas mensagens, dobrou a variedade, mas só
**11 de 20** passaram no checador — que roda fora do redator e impede o envio. E o modelo fraco
expôs um defeito real: respondeu `NAO_SEI` seguido de uma citação, o que contava como resposta e
mostraria o *token* cru ao cliente. Trocar para um modelo hospedado é uma linha de configuração."

## Slide 16 — Qualidade de software · ~45 s

"A meta era cobrir 80% das funções críticas; medi por função, não por linha. As 152 funções
críticas passaram de 90,3% para **100% executadas**, com 802 testes, incluindo os três fluxos de
ponta a ponta. A medição achou o que o projeto afirmava sem evidência: os *parsers* de PDF e DOCX e
os clientes reais do Whisper e da Anthropic nunca tinham rodado. Na carga: de 1 a 400 conversas
simultâneas, **zero erros**, p95 de 0,32 s com 100 — num processo, em memória, sem LLM no turno."

## Slide 17 — Limitações · ~45 s

"As limitações, todas no artigo. A maior: **não há dados operacionais reais** — modelos em dados
sintéticos, experimento simulado; afirmo direção, não magnitude. O braço LLM foi medido só com um
modelo local pequeno. A carga, sem LLM. Os canais são simulados. A voz é uma prova de conceito
reduzida, sem *barge-in* nem telefonia; a resposta curta é tão boa quanto o trecho que a
recuperação escolhe; e o microfone real não foi verificado. E os conjuntos
rotulados são pequenos."

## Slide 18 — Conclusão · ~45 s

"O trabalho entregou um sistema em que cada decisão — quem contatar, quando, por qual canal, o que
dizer e quando passar a uma pessoa — é tomada por um componente substituível, registrada com sua
causa e **medida contra um controle**. A hipótese se sustenta na forma: a predição melhora a
recuperação **na medida da escassez** da operação. Quem contata toda a carteira não ganha nada com
o módulo preditivo; quem alcança só uma fração ganha cerca de cinco pontos, a custo menor. A
automação sozinha reduz custo. E a voz funciona: responde em cerca de 2 segundos, com respostas de
cerca de 12 — depois que a medição mostrou que o *template* falado durava minutos."

## Slide 19 — Trabalhos futuros e encerramento · ~35 s

"Os próximos passos seguem das limitações: dados reais, de preferência de um ERP; um modelo que
preveja a **resposta** ao contato, não só o risco — os clientes mais arriscados são também os que
menos respondem; um modelo hospedado; e levar o mesmo agente a um número real por SIP, sem alterar
os agentes. Muito obrigado. Fico à disposição para as perguntas."

---

## Perguntas prováveis — respostas honestas

**"Se os dados são sintéticos, o que os modelos preditivos provam?"**
Que o protocolo funciona: *features* sem vazamento (`expanding().shift(1)`, `merge_asof`), *split*
cronológico 70/15/15, validação `TimeSeriesSplit`, seleção por precisão média. Não provam que o
modelo prevê pagamento real — o artigo e os relatórios dizem isso em destaque. A base Home Credit,
que daria comportamento real de pagamento, não pôde ser obtida (o download exige aceitar as regras
de uma competição encerrada em 2018 e não foi resolvido); está como limitação.

**"Por que uma simulação em vez de um experimento operacional?"**
Porque nenhuma base pública liga recebíveis a contatos de cobrança, e o ERP não foi obtido (decisão
D1). A simulação usa a curva de resposta real da Bank Marketing e trata todos os braços sob as mesmas
regras. O que afirmo é a **direção** e a **dependência da capacidade**, que resistiram à análise de
sensibilidade (6,3–6,5 p.p. se todos convertem à taxa média; 2,7–2,9 p.p. se o chat converte à
metade do telefone). As magnitudes pertencem ao simulador.

**"Os parâmetros do simulador não favorecem o seu sistema?"**
O *baseline* manual obedece a toda regra e nunca erra, o que o **favorece**; o custo dos braços
automatizados exclui infraestrutura e LLM, então é limite inferior. As suposições — R$ 2,50 por
ligação manual, capacidades de 2/4/8/16 contatos por dia, "subscribed" lido como "pagou" — estão
listadas no protocolo (`docs/experiment-protocol.md`, §3 e §7).

**"Por que não WhatsApp nem telefonia, como na proposta?"**
Exigiam conta comercial, aprovação de *templates* pela Meta e uma linha telefônica paga, e não
acrescentavam nenhuma decisão nova aos agentes — o risco estava previsto no cronograma e se
materializou. O contrato de canal permite trocar o chat por um adaptador de mensageria e levar a
chamada a um número por SIP sem tocar nos agentes; isso é argumento de projeto, não construído.

**"Por que um modelo de 3B? Isso não subestima o LLM?"**
Subestima, e é dito assim: é um **piso**. Não havia chave de provedor hospedado; o modelo local
roda sem conta e sem custo, e a temperatura 0 torna a execução reproduzível. Um modelo fraco ainda
foi útil: expôs o defeito do `NAO_SEI`. Medir um modelo hospedado é mudar `LLM_PROVIDER` e rodar
de novo — cada relatório registra provedor, modelo e data.

**"E a LGPD?"**
Identificadores diretos suprimidos; pseudonimização HMAC-SHA256 com chave derivada por *tenant*
(dois clientes não compartilham pseudônimos); quase-identificadores generalizados; transcrições com
janela de retenção de 180 dias (`make purge`); áudio da chamada apagável mantendo a transcrição.
Whisper e Piper rodam localmente porque a resposta lê a dívida de uma pessoa nomeada — um
provedor de nuvem viraria operador desse dado.

**"Por que a acurácia não é a métrica principal?"**
Porque com alvo desbalanceado prever sempre a classe majoritária tem acurácia alta e não serve para
nada: na tarefa 2, a classe majoritária tem 0,836 de acurácia e o modelo útil tem 0,762.

**"A chamada de voz é utilizável?"**
Como prova de conceito, sim, com ressalvas. O turno leva 2,2 s (2.193 ms medidos por WebRTC). A
primeira medição mostrou que a resposta do *template*, falada, durava 102 s; a chamada passou a
declarar um orçamento de 43 palavras (20 s × 2,15 palavras/s, a velocidade mais lenta medida do
Piper), o respondedor escolhe a seção que cabe sem parafrasear, e a resposta falada caiu para uma
mediana de **11,7 s** <!-- VOZ_FALA --> (máximo 16,2 s; 18 de 18 dentro da meta de 20 s). As
ressalvas: a resposta curta é tão boa quanto o trecho que a recuperação escolhe, e pode escolher o
errado; não há *barge-in*; e o caminho com uma pessoa falando no microfone do navegador não foi
verificado por máquina.

**"Os testes cobrem chamadas reais a serviços externos?"**
Não, de propósito: os casos que importam — tempo esgotado, recusa, reconhecedor que erra um valor —
não podem ser provocados num serviço real. As fronteiras são exercitadas com SDKs falsos que imitam
a forma da resposta real.
