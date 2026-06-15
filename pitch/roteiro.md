# Roteiro da apresentação — Sistema Multiagente Inteligente de Cobrança

> Pitch de **3–5 min** (banca + sala de aula). Tempo-alvo deste roteiro: **~4 min 30 s**.
> O texto em *itálico entre colchetes* é instrução de palco (o que mostrar / fazer), não para falar.
> Este roteiro completo também está nas **notas de cada slide** (tecla `S` no navegador) em versão resumida.

**Dicas de entrega:** fale devagar nos números; faça uma pausa curta ao trocar de slide;
no slide 4 (arquitetura) avance com `→` revelando uma camada de cada vez enquanto explica.

---

## Slide 1 — Capa  ·  ~15 s

*[Slide de abertura no projetor. Olhe para a banca antes de começar.]*

"Bom dia a todos. Meu nome é Gabriel Forster Rocha, e meu Trabalho de Conclusão de Curso
é um **Sistema Multiagente Inteligente de Cobrança**: um sistema que automatiza a recuperação
de crédito de ponta a ponta, combinando modelos de linguagem com RAG, agentes autônomos,
machine learning e serviços de voz. Nos próximos minutos eu vou apresentar o **contexto**, a
**proposta**, o que ela tem de **inovador** e por que existe **demanda de mercado** para isso."

## Slide 2 — Contexto  ·  ~35 s

*[Aponte para os dois números grandes ao citá-los.]*

"Vamos começar pelo contexto. A inadimplência no Brasil está no **maior patamar já registrado**.
Segundo a Serasa Experian, em dezembro de 2025 as empresas fecharam o ano com **213 bilhões de
reais em dívidas** e **8,9 milhões de CNPJs negativados** — um aumento de cerca de 2 milhões de
empresas em apenas um ano. Ou seja: nunca houve tanto crédito para recuperar e, ao mesmo tempo,
a capacidade de cobrar continua limitada."

## Slide 3 — O Problema  ·  ~35 s

"E por que recuperar esse crédito é tão difícil? Porque a cobrança, hoje, ainda é um processo
**majoritariamente manual**. A priorização é feita por **intuição**: sem dados, todo cliente é
tratado da mesma forma. Os **canais são fragmentados** — texto, áudio de WhatsApp, ligação — e
raramente conversam entre si. O **custo por contato é alto**, porque depende de operadores humanos,
que não escalam. E o processo é **reativo**: o sistema só age quando o cliente aparece — ou, muitas
vezes, não age."

## Slide 4 — Proposta (arquitetura)  ·  ~55 s

*[Avance com `→` revelando: orquestrador → os 3 agentes → camada de voz/serviços → camada de dados.]*

"A minha proposta ataca exatamente esses gargalos. É um **sistema multiagente**, coordenado por um
**orquestrador central**, que distribui o trabalho entre **três agentes especializados**.
O primeiro é o **agente responsivo**, que usa LLM com RAG para atender o cliente.
O segundo é o **agente proativo**, que detecta pendências e inicia a cobrança sozinho.
E o terceiro é o **agente preditivo**, de machine learning, que classifica os clientes e define a
prioridade de contato. Esses agentes se apoiam em duas camadas: uma **camada de voz e serviços**
— transcrição de áudio, telefonia, WhatsApp e base vetorial — e uma **camada de dados com auditoria**,
que garante rastreabilidade de cada decisão tomada pelo sistema."

## Slide 5 — Como funciona · Responsivo  ·  ~35 s

*[Acompanhe o fluxo da esquerda para a direita com o dedo/cursor.]*

"Vou detalhar os dois modos de operação. No **modo responsivo**, o cliente entra em contato.
Se a mensagem for um áudio, ela é **transcrita** por um serviço de speech-to-text. O sistema
identifica a **intenção**, busca o contexto na base de conhecimento interna usando **RAG**, e gera
a resposta com o **modelo de linguagem** — escalando para um atendente humano quando necessário.
O ponto-chave: as respostas são **ancoradas na documentação da empresa**, o que reduz drasticamente
o risco de alucinação."

## Slide 6 — Como funciona · Proativo + Preditivo  ·  ~35 s

"Já no **modo proativo**, o sistema age **antes do vencimento**. O modelo de machine learning usa
o histórico de dados para estimar a **propensão de pagamento** e o **risco de inadimplência**, e
gera um **ranking de prioridade**. Com esse ranking, o agente proativo aborda os clientes certos,
no momento certo, pelo canal certo — uma mensagem de WhatsApp ou uma ligação com voz sintetizada.
E se não houver resposta, ele **reagenda automaticamente** o contato, respeitando uma política de
backoff."

## Slide 7 — Inovação  ·  ~35 s

"E onde está a inovação? Não está em nenhuma dessas tecnologias **isoladamente** — está em **juntá-las**.
Primeiro: os três modos — responsivo, proativo e preditivo — funcionam **orquestrados** em um único
sistema, e não como três ferramentas soltas. Segundo: **voz de verdade**, cobrindo tanto o áudio de
WhatsApp quanto a telefonia. Terceiro: **respostas ancoradas** por RAG. E quarto, igualmente importante:
**conformidade por design** — o sistema respeita o Código de Defesa do Consumidor, a LGPD e os limites
legais de horário e frequência de contato."

## Slide 8 — Necessidade de mercado  ·  ~35 s

"Isso nos leva à **necessidade de mercado**. Estamos falando de um problema de **213 bilhões de reais**.
São 8,9 milhões de empresas inadimplentes, sendo que **96% são micro e pequenas** — justamente as que
têm menos estrutura para cobrar. A inadimplência se concentra em **serviços e comércio**. Varejo,
fintechs e escritórios de cobrança precisam **recuperar mais, gastando menos**. E as soluções
existentes hoje ou fazem **disparo em massa**, ou oferecem um **chatbot simples** — nenhuma prioriza
com machine learning nem cobre voz de ponta a ponta. É exatamente **essa lacuna** que o projeto ocupa."

## Slide 9 — Pergunta de pesquisa e fecho  ·  ~25 s

"Para fechar, a **pergunta de pesquisa** é: em que medida esse sistema multiagente é capaz de
**aumentar a eficiência** da recuperação de crédito frente aos processos convencionais? A hipótese
é que ele **reduz o tempo médio de recuperação** e **aumenta a taxa de resolução** de pendências.
E isso será avaliado de forma objetiva — com métricas de machine learning, como **F1 acima de 0,75**,
e com indicadores operacionais comparados a um **baseline**. Muito obrigado. Fico à disposição para
as perguntas."

---

### Possíveis perguntas da banca (preparação)

- **Como garantir conformidade (LGPD/CDC) na prática?** Limites de horário/frequência no orquestrador,
  consentimento para gravação, criptografia de dados pessoais e logs de auditoria de cada decisão.
- **E se o LLM responder errado?** RAG ancora a resposta na base interna; há escalonamento para humano
  e registro da interação.
- **Como provar que funciona?** Comparação contra baselines (processo manual e parcialmente automatizado),
  medindo taxa de recuperação e tempo médio de resolução, além de F1/precisão/revocação dos modelos.
- **Por que multiagente, e não um sistema único?** Separação de responsabilidades: cada agente tem um
  gatilho e um objetivo claros, é testável de forma isolada e pode evoluir sem quebrar os demais.
