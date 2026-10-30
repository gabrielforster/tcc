# RAG pipeline

Ingestion, chunking, indexing and retrieval evaluation for the knowledge base the
responsive agent answers from.

```sh
collection rag --retriever tfidf            # the lexical baseline, fitted in process
collection rag --retriever pgvector         # what actually serves, over a seeded tenant
collection rag --retriever tfidf --customer-only     # the comparable arms: both over
collection rag --retriever pgvector --customer-only  # the customer-facing corpus only
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
answer's citation pointing at the text that was actually shown. It was not a retrieval
quality decision, and the measurement below is why that distinction has to be made
carefully: on this corpus the move did not improve retrieval quality.

`collection rag --retriever tfidf` still builds the TF-IDF index, because that is the
lexical baseline the pretrained embedder has to be measured against;
`collection rag --retriever pgvector` measures the serving path itself.

## Pipeline

| Stage | Choice | Why |
|---|---|---|
| Loading | TXT, Markdown, CSV natively; PDF and DOCX lazily | PDF/DOCX pull heavy dependencies for a corpus that is mostly text, so they are imported only if such a file appears |
| CSV | One document per row in the baseline index; one versioned file on the serving path | A row must read standalone to retrieve on its own, which is why the index splits them — but a document is a file, so `publish_document` versions and cites the table as a whole. The two therefore chunk a table differently, and the measurement below shows it costing the serving path |
| Chunking | Paragraphs kept whole where they fit, 500–1000 words, 75 overlap | A chunk ending mid-sentence retrieves badly and reads worse when quoted back to a customer |
| Embedding | `paraphrase-multilingual-MiniLM-L12-v2` when serving; TF-IDF over word *and* character n-grams as the baseline | A fixed pretrained model means one vector space for every tenant and vectors that can be stored once; TF-IDF needs no API key or download, so the baseline still runs in CI |
| Index | Exact cosine over a normalised matrix | Hundreds of chunks, not millions — approximate search would add operational weight and a recall penalty for speed nobody needs |

Chunk sizes are counted in whitespace-delimited words. The real tokenizer depends on
whichever model eventually consumes the chunks, and pinning one now would be false
precision; for Portuguese the ratio is roughly 1.3 tokens per word, so the configured range
lands comfortably inside a typical context budget either way.

## Why TF-IDF stays, now that it no longer serves

It gives the paper a real lexical baseline to measure the neural retriever **against**.
Reporting "embeddings scored 0.80" says little; "embeddings scored 0.80 against a TF-IDF
baseline at 0.82 on the same questions and the same corpus" is a result — and, as the
measurement below shows, not the result that was expected. That is exactly why
`--retriever tfidf` keeps building it after the serving path moved to pgvector: a control
arm is only worth having if it is allowed to win.

## Results on the shipped corpus

Measured **2026-10-30**, both arms through the same `collection rag` code path, the same
`knowledge/questions.json`, the same ks and the same metric definitions. The only variable
is where passages come from.

* **tfidf** — the in-memory `VectorIndex`, TF-IDF over word and character n-grams, fitted
  to this corpus in this process.
* **pgvector** — the serving retriever, embedding
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dims), cosine
  distance in Postgres over the *Desenvolvimento* tenant's published chunks.

### Making the two arms comparable

This has to be settled before any number is read, because it decides which table is the
result.

`limites-de-alcada.md` is published `INTERNAL`, and the pgvector retriever filters on the
audience in SQL, so that document can never occupy a result slot — by design, because it
holds the discount ceilings. The in-memory index has no such notion and indexes all five
files. Two of the sixteen labelled questions (*"quando o atendimento precisa chamar um
humano?"* and *"qual o valor minimo de cada parcela?"*) are answered only by that file, so
they are unavoidable misses for the serving arm.

Withholding the document from the *labels* alone does not fix this, and the first version
of this measurement got it wrong that way: the baseline still ranks the internal document,
spends rank 1 on it for two questions, and is then scored as having missed — a handicap,
not an equalisation. **`--customer-only` withholds it from the corpus as well**, so both
arms rank the same four customer-facing documents, and the same two unanswerable questions
are dropped from both. That is the comparable measurement.

The arms still chunk differently, and that difference is real rather than procedural: the
index makes one document per CSV row (5 documents → 9 chunks, 4 → 8 under
`--customer-only`), while the database versions a file as a whole (5 documents →
5 chunks). It is part of what is being compared, and it turns out to matter.

### Full question set (16 questions) — not a comparison

| k | precision@k (tfidf) | recall@k (tfidf) | MRR (tfidf) | precision@k (pgvector) | recall@k (pgvector) | MRR (pgvector) |
|--:|--:|--:|--:|--:|--:|--:|
|  1 | 0.6250 | 0.5833 | 0.6250 | 0.5625 | 0.5625 | 0.5625 |
|  3 | 0.3750 | 0.9062 | 0.7708 | 0.3125 | 0.7917 | 0.6875 |
|  5 | 0.2500 | 1.0000 | 0.7833 | 0.2000 | 0.8229 | 0.7031 |
| 10 | 0.1250 | 1.0000 | 0.7833 | 0.1000 | 0.8229 | 0.7031 |

This is each arm against the question set as written, and it is reported for completeness
only. pgvector's 0.8229 is not a shortfall to explain: it is the arithmetic ceiling for any
retriever that cannot return the withheld document — `(12·1 + 1/2 + 2/3)/16 = 0.8229`. The
gap is the audience filter, not the embedder, and the table must not be read as a quality
comparison.

### Customer-facing corpus (14 questions) — the comparison

Both arms over the same four documents, `--customer-only` on each.

| k | precision@k (tfidf) | recall@k (tfidf) | MRR (tfidf) | precision@k (pgvector) | recall@k (pgvector) | MRR (pgvector) |
|--:|--:|--:|--:|--:|--:|--:|
|  1 | 0.7143 | 0.6429 | 0.7143 | 0.6429 | 0.6429 | 0.6429 |
|  3 | 0.3333 | 0.8571 | 0.7857 | 0.3571 | 0.9286 | 0.7857 |
|  5 | 0.2286 | 1.0000 | 0.8214 | 0.2286 | 1.0000 | 0.8036 |
| 10 | 0.1143 | 1.0000 | 0.8214 | 0.1143 | 1.0000 | 0.8036 |

**The pretrained embedder does not beat the lexical baseline on this corpus.** TF-IDF is
ahead on MRR — 0.8214 against 0.8036, a difference of 0.018 — and puts a gold document
first on 10 of 14 questions against pgvector's 9. The result went the other way from the
hypothesis this document wrote down in advance, and it is reported as measured.

It is a mixed result rather than a rout, and the mixture is the interesting part:

| | tfidf | pgvector | ahead |
|---|--:|--:|---|
| MRR@5 | **0.8214** | 0.8036 | tfidf, by 0.018 |
| MRR@3 | 0.7857 | 0.7857 | tie |
| recall@1 | 0.6429 | 0.6429 | tie |
| questions with a gold document at rank 1 | **10 / 14** | 9 / 14 | tfidf, by one question |
| recall@3 | 0.8571 | **0.9286** | pgvector |
| recall@5 | 1.0000 | 1.0000 | tie |

(recall@1 ties while the rank-1 counts differ because two questions have two gold
documents each: TF-IDF's extra rank-1 hits are on those, and score 0.5 apiece.)

So the embedder is *better at getting the answer into the top three* and *worse at putting
it first*. On fourteen questions over four documents, a 0.018 MRR difference is a quarter
of a reciprocal-rank point in total — about what one question moving from second place to
fourth would cost — which is well inside what a corpus this size can resolve. **The honest
statement is that the two retrievers are within noise of each other here, with the lexical
baseline nominally ahead.** Nothing in these numbers justifies the move to pgvector on
retrieval quality; the move is justified by what the in-memory index cannot do at all —
serve many tenants, survive an edit, and cite the text a customer was actually shown.

## What changed, question by question

Before this measurement, this document named six questions where the lexical baseline put
the wrong document first and predicted that a neural retriever should win exactly those,
because the gap there was semantic rather than lexical. Keeping the prediction next to the
outcome is the point of having written it down, so here is the outcome, for those
questions and for every other one where the two arms disagree. Both columns are over the
customer-facing corpus; a bold entry is a hit.

| question | tfidf@1 | pgvector@1 | gold |
|---|---|---|---|
| voces podem me ligar no domingo? | faq.md | **politica-de-cobranca.md** | politica-de-cobranca.md |
| em quantas vezes consigo dividir um atraso de 45 dias? | faq.md | **tabela-de-parcelamento.csv** | tabela-de-parcelamento.csv |
| nao quero mais receber mensagens de cobranca | **politica-de-cobranca.md** | **politica-de-cobranca.md** | politica-de-cobranca.md |
| posso parcelar minha divida? | **script-de-negociacao.md** | faq.md | tabela-de-parcelamento.csv, script-de-negociacao.md |
| quantas vezes voces podem me contatar por semana? | **politica-de-cobranca.md** | tabela-de-parcelamento.csv | politica-de-cobranca.md |
| nao reconheco essa divida, o valor esta errado | **script-de-negociacao.md** | tabela-de-parcelamento.csv | politica-de-cobranca.md, script-de-negociacao.md |
| qual o desconto para pagamento a vista? | script-de-negociacao.md | faq.md | tabela-de-parcelamento.csv |
| como comecar a conversa de cobranca por telefone? | faq.md | politica-de-cobranca.md | script-de-negociacao.md |

The prediction partly held. *"Voces podem me ligar no domingo?"* against a policy that says
*"não há contato aos domingos"* is the case the hypothesis was built on, and the embedder
does fix it where TF-IDF still answers `faq.md`; it also fixes the 45-day instalment
lookup. But *"nao quero mais receber mensagens de cobranca"*, the other question the
hypothesis rested on, was never a semantic failure at all — TF-IDF had ranked the internal
document first, and once that document is withheld from both arms the baseline gets it
right unaided. One of the two supporting cases was an artefact of the corpus, not of the
representation.

Against those two genuine fixes, the embedder loses three questions the baseline gets
right: `posso parcelar minha divida?`, `quantas vezes voces podem me contatar por semana?`
and `nao reconheco essa divida`. Two of the three drift to `tabela-de-parcelamento.csv`,
and **the likeliest explanation for those two is the chunking difference rather than the
embedder** -- likeliest, not measured, because the experiment that would settle it has not
been run. The instalment table is one versioned document in the database and is embedded
whole, so its vector is a blurred average of every band and sits weakly close to a great
many questions; the in-memory index splits it row by row and gives each band a sharp vector
of its own. The third, `posso parcelar minha divida?`, drifts to `faq.md` instead, which
that mechanism does not explain. The two retrievers are not only different
representations, they are reading differently shaped documents, and this comparison cannot
separate the two causes.

**So the next thing to try is chunking, not the model.** Splitting a published CSV into a
chunk per row — which `chunk_text` already does for prose and which the versioning model
allows, since chunks belong to a version — would put the serving path on the same document
shape the baseline enjoys, and would test the embedder on its own terms. That experiment is
not in this measurement, and until it is run, the comparison above is between a
representation *and* a chunking at once, which is the main thing limiting what it can
conclude.

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
