# RAG pipeline

Ingestion, chunking, indexing and retrieval evaluation for the knowledge base the
responsive agent answers from.

```sh
collection rag --retriever tfidf            # the lexical baseline, fitted in process
collection rag --retriever pgvector         # what actually serves, over a seeded tenant
collection rag --query "posso parcelar?"    # single query
```

The pgvector arm reads a tenant's published knowledge, so it needs one: `make seed` first.

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

`collection rag --retriever tfidf` still builds the TF-IDF index, because that is the
lexical baseline the pretrained embedder has to be measured against;
`collection rag --retriever pgvector` measures the serving path itself.

## Pipeline

| Stage | Choice | Why |
|---|---|---|
| Loading | TXT, Markdown, CSV natively; PDF and DOCX lazily | PDF/DOCX pull heavy dependencies for a corpus that is mostly text, so they are imported only if such a file appears |
| CSV | One document per row, column names kept in each row's text | The tables here — instalment bands, fee schedules — are looked up row-wise, and a row must read standalone to retrieve on its own |
| Chunking | Paragraphs kept whole where they fit, 500–1000 words, 75 overlap | A chunk ending mid-sentence retrieves badly and reads worse when quoted back to a customer |
| Embedding | `paraphrase-multilingual-MiniLM-L12-v2` when serving; TF-IDF over word *and* character n-grams as the baseline | A fixed pretrained model means one vector space for every tenant and vectors that can be stored once; TF-IDF needs no API key or download, so the baseline still runs in CI |
| Index | Exact cosine over a normalised matrix | Hundreds of chunks, not millions — approximate search would add operational weight and a recall penalty for speed nobody needs |

Chunk sizes are counted in whitespace-delimited words. The real tokenizer depends on
whichever model eventually consumes the chunks, and pinning one now would be false
precision; for Portuguese the ratio is roughly 1.3 tokens per word, so the configured range
lands comfortably inside a typical context budget either way.

## Why TF-IDF stays, now that it no longer serves

It gives the paper a real lexical baseline to measure the neural retriever **against**.
Reporting "embeddings scored 0.82" says little; "embeddings scored 0.82 against a TF-IDF
baseline at 0.71 on the same questions" is a result. That is why `--retriever tfidf`
keeps building it after the serving path moved to pgvector: it is the control arm, not a
placeholder that was left behind.

## Results on the shipped corpus

Measured **2026-10-30**, both arms through the same `collection rag` code path, the same
`knowledge/questions.json`, the same ks and the same metric definitions. The only variable
is where passages come from.

* **tfidf** — the in-memory `VectorIndex`, TF-IDF over word and character n-grams, fitted
  to this corpus in this process.
* **pgvector** — the serving retriever, embedding
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dims), cosine
  distance in Postgres over the *Desenvolvimento* tenant's published chunks.

### The two arms do not see the same corpus

This has to be said before any number is read.

`limites-de-alcada.md` is published `INTERNAL`, and the pgvector retriever filters on the
audience, so that document can never be returned by it — by design, because it holds the
discount ceilings. Two of the sixteen labelled questions (*"quando o atendimento precisa
chamar um humano?"* and *"qual o valor minimo de cada parcela?"*) are answered only by
that file, so they are unavoidable misses for the serving arm, and two more list it among
several relevant documents. The arms also chunk differently: the index makes one document
per CSV row (5 documents → 9 chunks), while the database versions a file as a whole
(5 documents → 5 chunks).

So two tables are reported. The **full set** is what each arm scores on the labelled
questions as written. The **customer-visible subset** strips `limites-de-alcada.md` from
every gold set and drops the two questions that leaves with nothing to find — 14 questions
over the corpus both arms can actually return. **The subset is the like-for-like
comparison; the full set is not**, and the difference between them is a measure of the
audience filter, not of retrieval quality.

### Full question set (16 questions)

| k | precision@k (tfidf) | recall@k (tfidf) | MRR (tfidf) | precision@k (pgvector) | recall@k (pgvector) | MRR (pgvector) |
|--:|--:|--:|--:|--:|--:|--:|
|  1 | 0.6250 | 0.5833 | 0.6250 | 0.5625 | 0.5625 | 0.5625 |
|  3 | 0.3750 | 0.9062 | 0.7708 | 0.3125 | 0.7917 | 0.6875 |
|  5 | 0.2500 | 1.0000 | 0.7833 | 0.2000 | 0.8229 | 0.7031 |
| 10 | 0.1250 | 1.0000 | 0.7833 | 0.1000 | 0.8229 | 0.7031 |

pgvector cannot reach recall 1.0 here, and the gap is the withheld document, not the
embedder.

### Customer-visible subset (14 questions) — the like-for-like comparison

| k | precision@k (tfidf) | recall@k (tfidf) | MRR (tfidf) | precision@k (pgvector) | recall@k (pgvector) | MRR (pgvector) |
|--:|--:|--:|--:|--:|--:|--:|
|  1 | 0.5714 | 0.5357 | 0.5714 | 0.6429 | 0.6429 | 0.6429 |
|  3 | 0.3333 | 0.8571 | 0.7143 | 0.3571 | 0.9286 | 0.7857 |
|  5 | 0.2286 | 1.0000 | 0.7429 | 0.2286 | 1.0000 | 0.8036 |
| 10 | 0.1143 | 1.0000 | 0.7429 | 0.1143 | 1.0000 | 0.8036 |

**The pretrained embedder wins, on MRR, by 0.061 (0.8036 against 0.7429).** It also puts
the right document first on 9 of 14 questions instead of 8, and has the right document in
the top 3 on 13 instead of 12. Both arms reach recall@5 = 1.0: on a corpus of five
documents, five is nearly the whole shelf, so recall@5 is not a discriminating number here
and MRR remains the one to watch.

The win is real but small, and it is honest to say so: one position of rank, on fourteen
questions, over five documents. It is a result that points the right way, not one that
settles the question.

## What changed, question by question

Before this measurement, this document named six questions where the lexical baseline put
the wrong document first and predicted that a neural retriever should win exactly those,
because the gap there was semantic rather than lexical. Measured, that prediction half
held, and keeping the prediction next to the outcome is the point of having written it
down.

| question | tfidf@1 | pgvector@1 | should be |
|---|---|---|---|
| voces podem me ligar no domingo? | faq.md | **politica-de-cobranca.md** | politica-de-cobranca.md |
| nao quero mais receber mensagens de cobranca | limites-de-alcada.md | **politica-de-cobranca.md** | politica-de-cobranca.md |
| em quantas vezes consigo dividir um atraso de 45 dias? | faq.md | **tabela-de-parcelamento.csv** | tabela-de-parcelamento.csv |
| posso parcelar minha divida? | limites-de-alcada.md | faq.md | tabela-de-parcelamento.csv |
| qual o desconto para pagamento a vista? | script-de-negociacao.md | faq.md | tabela-de-parcelamento.csv |
| como comecar a conversa de cobranca por telefone? | faq.md | politica-de-cobranca.md | script-de-negociacao.md |
| quantas vezes voces podem me contatar por semana? | politica-de-cobranca.md | tabela-de-parcelamento.csv | politica-de-cobranca.md |
| nao reconheco essa divida, o valor esta errado | script-de-negociacao.md | tabela-de-parcelamento.csv | politica-de-cobranca.md / script-de-negociacao.md |

The two questions the hypothesis was built on — *"voces podem me ligar no domingo?"* against
a policy that says *"não há contato aos domingos"*, and the opt-out phrasing — are both
fixed, and that is the predicted mechanism working. But the embedder also broke two
questions the lexical baseline had right, both towards `tabela-de-parcelamento.csv`: a
table joined into one document and embedded whole has a diffuse vector that is weakly close
to many questions, where TF-IDF's row-per-document split gave each band its own sharp one.
That is a chunking consequence of the move to versioned documents, not an embedder defect,
and it is the first thing to try changing if these numbers are pushed further.

The relevance floor (`MIN_RELEVANCE = 0.05`) never bound in this measurement: the lowest
top-1 similarity any of the sixteen questions produced was 0.2312. Nothing was dropped for
being too weak, so the floor is not part of the result.

## Caveat on the corpus

The knowledge base under `knowledge/` is **written for this work, not extracted from a real
company**. It is realistic in shape — policy, FAQ, negotiation script, instalment table,
approval limits — and it is in Portuguese because that is what the agent will answer in and
because retrieval behaves differently across languages. But it is small (5 documents,
~660 words), so the numbers above characterise the pipeline, not a production corpus. With
documents this short, each file becomes a single chunk and the chunking configuration is
effectively untested by the corpus itself; the chunking behaviour is covered by tests
instead.
