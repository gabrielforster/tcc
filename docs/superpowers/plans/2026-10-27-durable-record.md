# Stage 1 — The Durable Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every piece of state the application depends on out of memory and off disk into Postgres, pgvector and object storage, behind a tenant-scoped repository layer that makes an unscoped query raise rather than return another tenant's rows.

**Architecture:** One shared schema; every domain table carries `tenant_id`. Access goes through `unit_of_work()`, which binds a tenant to the SQLAlchemy session and installs an ORM-execute guard that refuses any query touching a tenant-scoped mapper without one. Knowledge becomes versioned rows with pgvector embeddings computed once per version by a fixed pretrained model; conversations, turns, retrieval records and deliveries become rows written by the existing channel and agent; files become objects in S3-compatible storage with a row describing each one.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, psycopg 3, pgvector, sentence-transformers, boto3 + MinIO, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-multi-tenant-operations-platform-design.md` — §6.1, §6.2, §6.3, §6.4, §6.12, §6.13, §6.14, §7 (stage 1), §8.

## Global Constraints

- **Tenancy is a column.** Every domain table carries `tenant_id` directly, including tables reachable through a parent — a join is not a boundary (§6.1).
- **Nothing the application depends on lives on disk.** Rows and vectors in Postgres, files in object storage, ephemeral state in Redis. A local path is a development convenience and never a source of truth (§6.14).
- **Document versions are immutable.** Editing a knowledge document creates a new version; a version's chunks and vectors are computed once and never recomputed (§6.3, §6.13).
- **One embedding model for all tenants:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions, cosine distance (§6.12, §6.13).
- **No behaviour change for the customer.** Stage 1 moves state; it does not change what the agent says. The source footer and the template default are stage 2 and must still be present when stage 1 ends.
- **Tests that touch no rows stay infrastructure-free.** Database tests carry `@pytest.mark.db`; the rest of the suite runs on a clone with no services (§11).
- **No real customer data in the repository.** Unchanged.
- **English commit messages, conventional-commit prefixes, one branch and one PR for the whole stage.**

## Review Focus

Tests for each of these live in the task named; they are listed here because the spec implies them and no task's happy path exercises them.

1. **A query for a tenant-scoped entity with no tenant bound** must raise `UnscopedQuery`, not return every tenant's rows — Task 3.
2. **An object carrying a different tenant's `tenant_id`** passed to a bound scope must be refused on `add()`, not silently persisted under the wrong tenant — Task 3.
3. **A knowledge document edited twice** must leave the first version, its chunks and its vectors addressable, and past retrieval records must still point at what was read — Task 5.
4. **A retrieval that finds nothing above the relevance floor** must write a turn with no retrieval rows rather than no turn at all — Task 7.
5. **A delivery report that arrives out of order** (`ENTREGUE` after `LIDA`) must still raise `BackwardsStatus` once the ladder is a table, with the same monotonic guarantee the in-memory log gives — Task 8.

---

### Task 1: Migration tooling, services and the database test fixture

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_extensions.py`
- Create: `src/collection/db/__init__.py`, `src/collection/db/base.py`
- Modify: `pyproject.toml` (deps `alembic>=1.13`, `pgvector>=0.3`, `boto3>=1.34`, `sentence-transformers>=3.0`; pytest markers), `docker-compose.yml` (MinIO), `src/collection/config.py`, `.env.example`, `Makefile`, `src/collection/cli.py`
- Modify: `tests/conftest.py` (the database fixtures)
- Test: `tests/test_db_setup.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `collection.db.base.Base` (a `DeclarativeBase`); `collection.db.base.TenantScoped` mixin declaring `tenant_id: Mapped[UUID]` with `ForeignKey("tenants.id")`, indexed, not nullable; settings `database_url`, `s3_endpoint_url`, `s3_bucket`, `s3_access_key`, `s3_secret_key`, `embedding_model`, `embedding_dimensions`; pytest fixtures `db_engine` and `db` (a `Session`), and the `db` marker.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.db
def test_pgvector_extension_is_installed(db):
    assert db.execute(text("select extname from pg_extension where extname='vector'")).scalar() == "vector"

@pytest.mark.db
def test_migrations_run_to_head_and_back(db_engine):
    # upgrade head, downgrade base, upgrade head again — no error
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_db_setup.py -m db -v`
Expected: FAIL — no `db` fixture.

- [ ] **Step 3: Add the dependencies, the MinIO service and the settings**

Settings gain `s3_endpoint_url: str = "http://localhost:9000"`, `s3_bucket: str = "collection"`, `s3_access_key`, `s3_secret_key`, `embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"`, `embedding_dimensions: int = 384`, `test_database_url: str = ""`. MinIO goes in `docker-compose.yml` on ports 9000/9001 with a named volume, matching the style of the existing services. Register `markers = ["db: needs Postgres"]` in `[tool.pytest.ini_options]`.

- [ ] **Step 4: Wire Alembic**

`migrations/env.py` reads `settings.database_url` and `target_metadata = Base.metadata`. Migration `0001` issues `CREATE EXTENSION IF NOT EXISTS vector` and drops it on downgrade.

- [ ] **Step 5: Add the fixtures**

`db_engine` (session-scoped) connects to `settings.test_database_url or settings.database_url`, runs `alembic upgrade head`, and **skips** the test when the server is unreachable — unless `COLLECTION_REQUIRE_DB=1` is set, in which case it fails. CI sets that variable, so a CI run cannot pass by skipping. `db` yields a `Session` in a transaction rolled back after each test.

- [ ] **Step 6: Add `collection db upgrade` and `collection db downgrade` to the CLI and `make migrate` to the Makefile**

One CLI command per Makefile target, as every other command in `cli.py` does.

- [ ] **Step 7: Run tests**

Run: `docker compose up -d && uv run pytest tests/test_db_setup.py -m db -v && uv run pytest -m "not db" -q`
Expected: the db tests PASS; the rest of the suite PASSES untouched.

- [ ] **Step 8: Commit**

Commit the design document in this commit — it is stage 1's specification and belongs in the first commit of the branch.

```bash
git add alembic.ini migrations src/collection/db pyproject.toml docker-compose.yml src/collection/config.py .env.example Makefile src/collection/cli.py tests docs/superpowers/specs .gitignore
git commit -m "chore: add alembic, pgvector and object storage to the development stack"
```

---

### Task 2: Tenants and the lifecycle

**Files:**
- Create: `src/collection/db/models/__init__.py`, `src/collection/db/models/tenant.py`, `migrations/versions/0002_tenants.py`
- Test: `tests/test_tenants.py`

**Interfaces:**
- Consumes: `Base` from Task 1.
- Produces: `TenantState` (`StrEnum`: `PROVISIONED`, `INGESTING`, `READY`, `ACTIVE`, `SUSPENDED`); `Tenant` mapped class with `id: UUID`, `name: str`, `slug: str` (unique), `tenant_key: str` (unique, the public key the widget embed carries, §11), `state: TenantState`, `created_at`, `activated_at`; `Tenant.servable -> bool` (True only in `ACTIVE`); `advance(tenant, to: TenantState) -> None` raising `InvalidTransition` for any move not on the ladder.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_new_tenant_is_provisioned_and_not_servable(): ...
def test_the_ladder_advances_in_order(): ...  # PROVISIONED -> INGESTING -> READY -> ACTIVE
def test_skipping_a_rung_raises_invalid_transition(): ...  # PROVISIONED -> ACTIVE
def test_any_state_may_be_suspended_and_suspended_returns_to_ready(): ...
def test_activating_sets_activated_at(): ...
```

The transition table is pure logic and needs no database; only the persistence test carries `@pytest.mark.db`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_tenants.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `TenantState`, `Tenant`, `InvalidTransition` and `advance()` in `src/collection/db/models/tenant.py`**

`tenant_key` defaults to `secrets.token_urlsafe(16)`. Write migration `0002`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tenants.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: give a tenant a lifecycle the channel can refuse to serve"
```

---

### Task 3: The scoped repository layer and its guard

This is §6.1. Everything after it depends on it, and a reviewer should reject it if an unscoped query is possible.

**Files:**
- Create: `src/collection/db/session.py`, `src/collection/db/repository.py`
- Test: `tests/test_tenant_scope.py`

**Interfaces:**
- Consumes: `Base`, `TenantScoped`, `Tenant`.
- Produces:
  - `engine() -> Engine` and `session_factory() -> sessionmaker`, both cached.
  - `@contextmanager unit_of_work(tenant_id: UUID | None = None, *, platform: bool = False) -> Iterator[Session]` — commits on exit, rolls back on exception, binds `session.info["tenant_id"]`.
  - `UnscopedQuery(RuntimeError)`, `CrossTenantWrite(RuntimeError)`.
  - `class TenantScope` constructed as `TenantScope(session, tenant_id)` with `add(obj) -> None` (sets `tenant_id`, raises `CrossTenantWrite` when the object already carries a different one), `select(model) -> Select` (pre-filtered), `all(model, *criteria) -> list`, `get(model, id)` returning `None` for another tenant's row.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
@pytest.mark.parametrize("mapper", TENANT_SCOPED_MAPPERS, ids=lambda m: m.class_.__name__)
def test_every_tenant_scoped_mapper_is_unreachable_without_a_tenant(db, mapper):
    with pytest.raises(UnscopedQuery):
        db.execute(select(mapper.class_)).all()

@pytest.mark.db
def test_select_through_a_scope_returns_only_that_tenants_rows(): ...

@pytest.mark.db
def test_add_refuses_an_object_carrying_another_tenants_id(): ...  # CrossTenantWrite

@pytest.mark.db
def test_get_returns_none_for_another_tenants_row(): ...

@pytest.mark.db
def test_the_platform_scope_may_read_tenants_without_a_tenant_bound(): ...
```

`TENANT_SCOPED_MAPPERS` is derived at collection time from `Base.registry.mappers`, filtered to mappers whose table has a `tenant_id` column — so a table added later without a scope is a failing test, not a silent hole.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_tenant_scope.py -m db -v`
Expected: FAIL.

- [ ] **Step 3: Implement the guard**

A `do_orm_execute` event listener on the `Session` class raises `UnscopedQuery` when the statement's mappers include a tenant-scoped one and `session.info` carries no `tenant_id`, unless `session.info["platform"]` is set. Enforcement lives in this one listener; no call site may be trusted to filter.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tenant_scope.py -m db -v`
Expected: PASS, including one parametrised case per tenant-scoped mapper.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: enforce the tenant boundary in one place"
```

---

### Task 4: Customers, receivables and internal users

Schema only for internal users — authentication is stage 3. They are here because the tenant boundary test should cover them from the first migration that creates them.

**Files:**
- Create: `src/collection/db/models/customer.py`, `src/collection/db/models/internal_user.py`, `migrations/versions/0003_domain.py`
- Test: `tests/test_customer_rows.py`

**Interfaces:**
- Consumes: `TenantScope`, `TenantScoped`.
- Produces: `Customer` (tenant, `external_id`, `pseudonym`, `preferred_channel`, timestamps; unique on `(tenant_id, external_id)`); `Receivable` (tenant, customer, `amount: Numeric(12,2)`, `due_date`, `days_overdue`, `status`); `InternalUser` (tenant, `email`, `password_hash`, `role`, `active`; unique on `(tenant_id, email)`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_two_tenants_may_hold_the_same_external_customer_id(): ...
@pytest.mark.db
def test_a_receivable_carries_its_own_tenant_id_not_only_its_customers(): ...
@pytest.mark.db
def test_days_overdue_matches_the_due_date_at_ingestion(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_customer_rows.py -m db -v`

- [ ] **Step 3: Implement the three mapped classes and migration `0003`**

Column names follow `collection.domain.schema` wherever the same concept already has a name there.

- [ ] **Step 4: Run tests, including the scope guard**

Run: `uv run pytest tests/test_customer_rows.py tests/test_tenant_scope.py -m db -v`
Expected: PASS — the parametrised guard test now covers three more mappers without being edited.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: persist customers, receivables and internal users per tenant"
```

---

### Task 5: Knowledge as versioned rows with vectors in pgvector

**Files:**
- Create: `src/collection/db/models/knowledge.py`, `src/collection/rag/store.py`, `migrations/versions/0004_knowledge.py`
- Modify: `src/collection/rag/embeddings.py` (make `get_embedder` default to the sentence-transformer and cache the loaded model)
- Test: `tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: `TenantScope`, `chunk_text` from `collection.rag.chunking`, `Document` from `collection.rag.documents`.
- Produces:
  - `KnowledgeDocument` (tenant, `title`, `path`, `audience: Audience`, `current_version_id`), `Audience` (`CUSTOMER`, `INTERNAL`), `KnowledgeVersion` (tenant, document, `number: int`, `content_hash`, `body`, `created_at`), `KnowledgeChunk` (tenant, version, `ordinal`, `text`, `embedding: Vector(384)`, `word_count`).
  - `publish_document(scope, *, title, path, body, audience=Audience.CUSTOMER) -> KnowledgeVersion` — creates version 1, or version N+1 with new chunks and vectors, and returns unchanged when `content_hash` matches the current version.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_publishing_twice_with_the_same_body_does_not_create_a_second_version(): ...
@pytest.mark.db
def test_editing_creates_version_two_and_leaves_version_one_addressable(): ...
@pytest.mark.db
def test_each_chunk_of_a_version_carries_a_384_dimension_vector(): ...
@pytest.mark.db
def test_a_versions_chunks_belong_to_that_version_only(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_knowledge_store.py -m db -v`

- [ ] **Step 3: Implement the mapped classes, migration `0004` and `publish_document`**

The embedder is loaded once per process and shared; `content_hash` is SHA-256 of the normalised body.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_knowledge_store.py -m db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: version knowledge documents and store their vectors in pgvector"
```

---

### Task 6: Retrieval from the database

**Files:**
- Create: `src/collection/rag/retriever.py`
- Modify: `src/collection/rag/index.py` (docstring: `VectorIndex` is the evaluation-time in-memory index, not the serving path)
- Test: `tests/test_retriever.py`

**Interfaces:**
- Consumes: `KnowledgeChunk`, `TenantScope`, `MIN_RELEVANCE` and `TOP_K` as they are defined today.
- Produces: `PgVectorRetriever(scope)` with `search(query: str, top_k: int = TOP_K) -> list[Retrieved]`; `Retrieved` gains `chunk_id: UUID | None` and `version_id: UUID | None` (defaulting to `None`, so the in-memory index still constructs it) so Task 7 can write the retrieval record without a second query.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_search_returns_chunks_of_this_tenant_only(): ...
@pytest.mark.db
def test_search_scores_are_cosine_similarity_in_zero_to_one_descending(): ...
@pytest.mark.db
def test_search_only_reads_the_current_version_of_each_document(): ...
@pytest.mark.db
def test_search_excludes_internal_audience_documents(): ...
@pytest.mark.db
def test_a_query_matching_nothing_returns_an_empty_list(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_retriever.py -m db -v`

- [ ] **Step 3: Implement `PgVectorRetriever`**

Similarity is `1 - cosine_distance`, ordered by distance ascending, limited in SQL, filtered to the document's current version and `Audience.CUSTOMER`, then dropped below `MIN_RELEVANCE` in Python so the floor keeps the one definition it has today.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_retriever.py -m db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: retrieve passages from pgvector within a tenant"
```

---

### Task 7: Conversations, turns and the retrieval record

**Files:**
- Create: `src/collection/db/models/conversation.py`, `src/collection/db/transcripts.py`, `migrations/versions/0005_conversations.py`
- Test: `tests/test_transcripts.py`

**Interfaces:**
- Consumes: `TenantScope`, `Customer`, `KnowledgeVersion`, `KnowledgeChunk`, `Retrieved`.
- Produces:
  - `Conversation` (tenant, customer, `channel`, `state: ConversationState`, `holder`, `opened_at`, `closed_at`, `escalation_reason`), `ConversationState` (`OPEN`, `ESCALATED`, `CLOSED`).
  - `Turn` (tenant, conversation, `role`, `text`, `intent`, `confidence`, `responder`, `provider`, `model`, `latency_ms`, `created_at`).
  - `TurnRetrieval` (tenant, turn, knowledge version, chunk, `score`, `rank`).
  - `record_turn(scope, conversation, *, role, text, passages: list[Retrieved] = (), **fields) -> Turn` — writes the turn and one `TurnRetrieval` per passage, in one transaction.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_a_turn_with_passages_writes_one_retrieval_row_per_passage_with_its_rank(): ...
@pytest.mark.db
def test_a_turn_with_no_passages_is_still_written(): ...          # Review Focus 4
@pytest.mark.db
def test_a_retrieval_row_points_at_the_version_that_was_read_not_the_current_one(): ...
@pytest.mark.db
def test_a_conversation_keeps_its_turns_in_order_across_two_units_of_work(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_transcripts.py -m db -v`

- [ ] **Step 3: Implement the mapped classes, migration `0005` and `record_turn`**

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_transcripts.py -m db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: write conversations, turns and what was retrieved for each"
```

---

### Task 8: Deliveries as rows

**Files:**
- Create: `src/collection/db/models/delivery.py`, `migrations/versions/0006_deliveries.py`
- Modify: `src/collection/channels/delivery.py` (add `DatabaseDeliveryLog` beside `DeliveryLog`, sharing `DeliveryStatus`, `BackwardsStatus` and `_RANK`)
- Test: `tests/test_delivery_rows.py`

**Interfaces:**
- Consumes: `TenantScope`, `Conversation`, `Turn`.
- Produces: `Delivery` (tenant, conversation, turn, `status: DeliveryStatus`, `owner`, `sent_at`, `delivered_at`, `read_at`, `replied_at`); `DatabaseDeliveryLog(scope)` with the same `record()`, `advance()` and `get()` signatures the in-memory log has today.

- [ ] **Step 1: Write the failing tests**

Port the existing ladder tests in `tests/test_channels.py` against `DatabaseDeliveryLog`, including:

```python
@pytest.mark.db
def test_entregue_after_lida_raises_backwards_status(): ...        # Review Focus 5
@pytest.mark.db
def test_repeating_the_current_status_is_a_no_op(): ...
@pytest.mark.db
def test_an_unknown_delivery_id_raises_key_error(): ...
@pytest.mark.db
def test_each_rung_stamps_its_own_timestamp(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_delivery_rows.py -m db -v`

- [ ] **Step 3: Implement `Delivery`, migration `0006` and `DatabaseDeliveryLog`**

The monotonic rule stays in one place: both logs compare `_RANK`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_delivery_rows.py tests/test_channels.py -v`
Expected: PASS — the in-memory log's behaviour is unchanged.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: persist the delivery ladder"
```

---

### Task 9: The channel writes through the record

The behaviour the customer sees does not change. What changes is that a restart no longer loses the conversation.

**Files:**
- Modify: `src/collection/channels/session.py` (add `DatabaseSessionStore`), `src/collection/channels/web.py` (`ChatChannel` takes a retriever and a store rather than building its own), `src/collection/api/chat.py` (`ChatService.load` resolves the tenant and builds the database-backed channel)
- Test: `tests/test_web_chat_persistence.py`

**Interfaces:**
- Consumes: `PgVectorRetriever`, `record_turn`, `DatabaseDeliveryLog`, `Tenant.servable`.
- Produces: `DatabaseSessionStore(scope)` with `open()`, `get()`, `close()` matching `SessionStore`; `ChatChannel(retriever, responder, store, delivery)`; `resolve_tenant(tenant_key: str) -> Tenant` raising `UnknownTenant`; `TenantNotServable(SessionError)` raised by `open()` for any tenant not `ACTIVE` (§6.2).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_a_transcript_survives_a_new_store_over_the_same_database(): ...
@pytest.mark.db
def test_opening_a_session_for_a_non_active_tenant_raises_tenant_not_servable(): ...
@pytest.mark.db
def test_an_answer_writes_a_customer_turn_an_agent_turn_and_its_retrieval_rows(): ...
@pytest.mark.db
def test_an_unknown_tenant_key_is_refused(): ...
@pytest.mark.db
def test_the_proactive_agent_schedules_nothing_for_a_non_active_tenant(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_web_chat_persistence.py -m db -v`

- [ ] **Step 3: Implement the store, the channel wiring and tenant resolution**

`TenantNotServable` maps to HTTP 403 in `_STATUS`. The tenant key arrives as a field on `SessionRequest`; a request without one falls back to `settings.default_tenant_key` so the demo page keeps working.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS — `tests/test_web_chat.py` and `tests/test_api.py` still pass against the in-memory path.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: run the chat channel on the durable record"
```

---

### Task 10: Object storage

**Files:**
- Create: `src/collection/db/models/stored_object.py`, `src/collection/storage/objects.py`, `migrations/versions/0007_objects.py`
- Test: `tests/test_object_store.py`

**Interfaces:**
- Consumes: `TenantScope`, settings from Task 1.
- Produces: `StoredObject` (tenant nullable for platform artifacts, `kind`, `key`, `content_type`, `size_bytes`, `created_at`); `ObjectStore(scope=None)` with `put(key: str, data: bytes, *, kind: str, content_type: str) -> StoredObject`, `get(key: str) -> bytes`, `exists(key) -> bool`; `ObjectStoreError`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_put_writes_the_object_and_a_row_describing_it(): ...
@pytest.mark.db
def test_get_returns_exactly_what_put_stored(): ...
@pytest.mark.db
def test_a_tenant_scoped_store_cannot_read_another_tenants_key(): ...
@pytest.mark.db
def test_a_missing_key_raises_object_store_error_not_a_boto_exception(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_object_store.py -m db -v`

- [ ] **Step 3: Implement `ObjectStore` over boto3 against MinIO**

Keys are `tenants/<tenant_id>/<kind>/<name>` for tenant objects and `platform/<kind>/<name>` for platform artifacts; the bucket is created on first use if absent.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_object_store.py -m db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: store files in object storage with a row describing each one"
```

---

### Task 11: The pipeline writes to the record, not to disk

**Files:**
- Create: `src/collection/db/models/platform_model.py`, `migrations/versions/0008_platform_models.py`
- Modify: `src/collection/data/ingest.py`, `src/collection/models/train.py`, `src/collection/models/report.py`, `src/collection/models/explain.py`, `src/collection/cli.py`
- Test: `tests/test_pipeline_storage.py`

**Interfaces:**
- Consumes: `ObjectStore`, `TenantScope`, `Customer`, `Receivable`.
- Produces: `PlatformModel` (no tenant: `kind`, `version`, `trained_at`, `metrics: JSONB`, `artifact_key`); `CustomerScore` (tenant, customer, platform model, `score`, `scored_at`); `ingest(scope, source) -> IngestReport` writing customers and receivables as rows and the raw frame as an object; `train(...)` writing the artifact, metrics and SHAP plots as objects and one `PlatformModel` row.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_ingest_writes_customers_and_receivables_for_the_tenant(): ...
@pytest.mark.db
def test_ingest_stores_the_raw_frame_as_an_object_and_writes_no_file(tmp_path): ...
@pytest.mark.db
def test_training_records_a_platform_model_row_with_an_artifact_key(): ...
@pytest.mark.db
def test_scoring_writes_one_score_per_customer_against_that_model_version(): ...
```

The "writes no file" assertion is the one that keeps §6.14 honest: it asserts the configured data directories are empty after a run.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_pipeline_storage.py -m db -v`

- [ ] **Step 3: Implement the models, migration `0008` and the rewiring**

Pandas still does the work in memory; only the read and write ends change. Local directory settings remain for a developer who asks for a file explicitly, and nothing in the serving path reads them.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -m db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: run the pipeline against object storage and the record"
```

---

### Task 12: Seeding a development tenant, and saying so in the docs

**Files:**
- Modify: `src/collection/cli.py`, `Makefile`, `README.md`, `docs/architecture.md`, `docs/rag.md`, `docs/web-chat.md`, `.env.example`
- Create: `src/collection/db/seed.py`
- Test: `tests/test_seed_tenant.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `seed_development_tenant(name: str = "Desenvolvimento") -> Tenant` — creates the tenant, publishes every file under `knowledge/` through `publish_document` with `Audience.INTERNAL` for `limites-de-alcada.md` and `Audience.CUSTOMER` for the rest, ingests the synthetic source, and advances the tenant to `ACTIVE`; CLI command `collection tenant seed`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_seeding_produces_an_active_tenant_that_can_answer(): ...
@pytest.mark.db
def test_the_authority_limits_document_is_seeded_as_internal(): ...
@pytest.mark.db
def test_seeding_twice_does_not_duplicate_documents_or_versions(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_seed_tenant.py -m db -v`

- [ ] **Step 3: Implement `seed_development_tenant` and the CLI command**

- [ ] **Step 4: Update the documentation**

`README.md` gains the new first-run steps (`docker compose up -d`, `make migrate`, `collection tenant seed`). `docs/architecture.md` describes the record and the scope guard. `docs/rag.md` states that retrieval now runs on pgvector with a pretrained embedder, and that the numbers published there were measured against the TF-IDF index and are re-measured in this branch. `docs/web-chat.md` notes that sessions survive a restart.

- [ ] **Step 5: Run everything**

Run: `uv run pytest -v && uv run ruff check src tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: seed a development tenant from the knowledge directory"
```

---

### Task 13: Re-measure retrieval against the new retriever

§6.13 says the re-measurement is part of the stage that lands it, not deferred. This task is the one that makes the published numbers true.

**Files:**
- Modify: `src/collection/rag/evaluate.py`, `docs/rag.md`, `docs/responder-comparison.md`
- Test: `tests/test_rag.py` (extend)

**Interfaces:**
- Consumes: `PgVectorRetriever`, the existing evaluation question set.
- Produces: `collection rag --retriever pgvector` running the existing metrics against the database.

- [ ] **Step 1: Extend the evaluation to take a retriever**

The question set, the metrics and their definitions do not change — only where passages come from.

- [ ] **Step 2: Run both arms**

Run: `uv run collection rag --retriever tfidf` and `uv run collection rag --retriever pgvector`
Expected: two tables of the same shape.

- [ ] **Step 3: Rewrite the numbers in `docs/rag.md`**

Report both, dated, naming the embedding model. If the pretrained embedder scores worse than TF-IDF on this corpus, say so and keep the number — a retrieval result that went the wrong way is a finding, and the paper already promised the comparison.

- [ ] **Step 4: Note the consequence in `docs/responder-comparison.md`**

The responder comparison's retrieval changed; its figures are re-measured or explicitly marked as measured against the old retriever, with the date.

- [ ] **Step 5: Commit**

```bash
git commit -m "docs: re-measure retrieval against the pgvector retriever"
```
