# RAG pipeline

Ingestion, chunking, indexing and retrieval evaluation for the knowledge base the
responsive agent answers from.

```sh
collection rag                              # build the index and evaluate retrieval
collection rag --query "posso parcelar?"    # single query
```

## Why retrieval is measured on its own

An agent that invents an answer because nothing relevant was retrieved has a **retrieval**
failure, not a generation one. Measuring the two together makes them indistinguishable, and
the hallucination numbers the paper will report are only meaningful if retrieval quality is
already known. So retrieval is evaluated against a labelled question set before any LLM is
involved.

## Where retrieval actually runs

Serving no longer uses the in-memory index described below. Knowledge is published as
versioned rows and each chunk is embedded once, at publish time, by a **pretrained
sentence-transformer**; a question is retrieved by cosine distance in **pgvector**, in one
statement that also carries the tenant predicate, the current-version filter and the
customer-audience filter — see `src/collection/rag/retriever.py`.

The reason for the move is not speed. A TF-IDF vocabulary is fitted to one corpus in one
process, so the same sentence meant a different vector in every tenant and any edit
invalidated the whole matrix; a fixed model gives every tenant one space and lets a
version's vectors be written once and never recomputed, which is what keeps an old
answer's citation pointing at the text that was actually shown.

`collection rag` still builds the TF-IDF index, because that is the lexical baseline the
pretrained embedder has to be measured against.

## Pipeline

| Stage | Choice | Why |
|---|---|---|
| Loading | TXT, Markdown, CSV natively; PDF and DOCX lazily | PDF/DOCX pull heavy dependencies for a corpus that is mostly text, so they are imported only if such a file appears |
| CSV | One document per row, column names kept in each row's text | The tables here — instalment bands, fee schedules — are looked up row-wise, and a row must read standalone to retrieve on its own |
| Chunking | Paragraphs kept whole where they fit, 500–1000 words, 75 overlap | A chunk ending mid-sentence retrieves badly and reads worse when quoted back to a customer |
| Embedding | TF-IDF over word *and* character n-grams | No API key, no model download, runs in CI; character n-grams handle Portuguese morphology without a stemmer |
| Index | Exact cosine over a normalised matrix | Hundreds of chunks, not millions — approximate search would add operational weight and a recall penalty for speed nobody needs |

Chunk sizes are counted in whitespace-delimited words. The real tokenizer depends on
whichever model eventually consumes the chunks, and pinning one now would be false
precision; for Portuguese the ratio is roughly 1.3 tokens per word, so the configured range
lands comfortably inside a typical context budget either way.

## Why TF-IDF stays, now that it no longer serves

It gives the paper a real lexical baseline to measure the neural retriever **against**.
Reporting "embeddings scored 0.82" says little; "embeddings scored 0.82 against a TF-IDF
baseline at 0.71 on the same questions" is a result. That is why `collection rag` keeps
building it after the serving path moved to pgvector: it is the control arm, not a
placeholder that was left behind.

## Results on the shipped corpus

> [!NOTE]
> The numbers below were measured against the **TF-IDF index**, which is no longer what
> serves a question. They are superseded by the measurement of the pgvector retriever in
> this branch, and they stay here until that measurement replaces them — as the baseline
> the new numbers are read against, not as a description of the serving path.

5 documents, 9 chunks, 16 labelled questions:

|   k |   precision@k |   recall@k |    mrr |
|----:|--------------:|-----------:|-------:|
|   1 |         0.625 |     0.583  | 0.625  |
|   3 |         0.375 |     0.906  | 0.771  |
|   5 |         0.250 |     1.000  | 0.783  |
|  10 |         0.125 |     1.000  | 0.783  |

Recall@5 is 1.0: the right document is always in the top five. Precision falls with k by
construction — most questions have one relevant document, so precision@5 cannot exceed 0.2
for them, and it is reported for completeness rather than as a quality signal.

**MRR is the number to watch, and 0.78 is the honest weak spot.**

## Known weakness of the lexical baseline

Six of sixteen questions do not rank the right document first:

| question | retrieved first | should be |
|---|---|---|
| posso parcelar minha divida? | limites-de-alcada.md | tabela-de-parcelamento.csv |
| em quantas vezes consigo dividir um atraso de 45 dias? | faq.md | tabela-de-parcelamento.csv |
| qual o desconto para pagamento a vista? | script-de-negociacao.md | tabela-de-parcelamento.csv |
| voces podem me ligar no domingo? | faq.md | politica-de-cobranca.md |
| nao quero mais receber mensagens de cobranca | limites-de-alcada.md | politica-de-cobranca.md |
| como comecar a conversa de cobranca por telefone? | faq.md | script-de-negociacao.md |

The pattern is consistent and worth stating plainly: these are questions where the answer
is **semantic rather than lexical**. "Voces podem me ligar no domingo?" shares no
distinctive term with a policy that says *"não há contato aos domingos"* beyond the word
itself, while the FAQ is longer and shares more common vocabulary. A lexical retriever
cannot close that gap.

This is exactly the case a neural retriever should win, which makes it a good experiment
rather than a problem to hide: the comparison has a concrete hypothesis and six specific
questions to test it on, instead of a vague expectation that embeddings are better.

## Caveat on the corpus

The knowledge base under `knowledge/` is **written for this work, not extracted from a real
company**. It is realistic in shape — policy, FAQ, negotiation script, instalment table,
approval limits — and it is in Portuguese because that is what the agent will answer in and
because retrieval behaves differently across languages. But it is small (5 documents,
~660 words), so the numbers above characterise the pipeline, not a production corpus. With
documents this short, each file becomes a single chunk and the chunking configuration is
effectively untested by the corpus itself; the chunking behaviour is covered by tests
instead.
