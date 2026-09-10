# Real data acquisition — plan and decision record

> Status: **decided on 2026-09-10** (D1 below). The project proceeds on public datasets
> now; the company ERP stays open as a parallel track that would upgrade the results if
> it lands in time. Decisions D2-D6 remain open and are tracked at the bottom.

## Decision in one paragraph

The predictive module will be built and evaluated on **public datasets** — Home Credit
Default Risk for payment behavior, UCI Bank Marketing for contact strategy — with the
synthetic generator kept as a test fixture. This unblocks the predictive module
immediately instead of waiting on an authorization that has already slipped. The cost is
stated plainly and carried into the paper: without collection events tied to receivables,
the operational comparison against conventional collection becomes a documented limitation
rather than a measured result. If ERP access arrives before mid-October, Track A resumes
as the headline result and the public-data work becomes the reproducible benchmark — no
work is wasted either way.

## 1. Where we stand

The pipeline is complete and runs end to end, but every number it has produced so far
comes from `SyntheticSource` — a generator I wrote, seeded at 42, producing 1,200
customers and 12,000 receivables. No ERP was touched.

That matters more than it might look. The patterns visible in `docs/eda.md` are ones the
generator was built to contain:

| What the chart shows | Where it actually comes from |
|---|---|
| January/February and July delinquency spikes | the `pressure` term in `_make_invoices` |
| Wholesale and large customers default less | the propensity bonus in `_make_customers` |
| Long right tail of days late | a gamma delay whose scale is tied to latent propensity |

So the charts demonstrate that the pipeline works. They are **not evidence about
collection behavior**, and the same will be true of any model metric from the predictive
module: it would measure whether XGBoost can recover a process I designed, which it can,
and which proves nothing about credit recovery.

The research question asks whether the system improves recovery **compared to
conventional approaches**. That comparison is not answerable on synthetic data at all.
Real data is not a nice-to-have here — it is what separates a working prototype from a
defensible result.

## 2. What the data actually has to contain

Two predictive tasks and one operational comparison, each with different requirements:

| Need | Minimum content | Why |
|---|---|---|
| Task 1 — payment propensity | Receivables with due date and payment date, per customer, over time | The target is "settled within 30 days of due date" |
| Task 2 — future default | The same, with enough history before issue date | Features must be computable at issue time |
| Customer history features | Several receivables per customer, chronologically ordered | `hist_*` features are meaningless with one invoice per customer |
| Operational baseline | Collection contact attempts with channel, timestamp and outcome | The whole comparison against "conventional collection" depends on this |
| Legal compliance | A lawful basis and an anonymization path | LGPD, and the paper has to describe it |

The fourth row is the hard one. **No public dataset contains collection contact events
tied to receivables.** That combination exists only inside a company that actually
collects debt. This is the single most important constraint on everything below.

## 3. Track A — the company ERP (parallel, not blocking)

The only source that satisfies all five rows. `ERPSource` already has the queries written
against the domain schema; the anonymization layer (drop direct identifiers, HMAC
pseudonyms for keys, generalized quasi-identifiers) is built and tested.

Sequence, with the work that is already done marked:

1. **Identify the system and the owner.** Which ERP, who administers it, who can authorize
   a read-only extract. *(needs you)*
2. **Get written authorization.** A short term signed by the company allowing use of
   anonymized operational data in an academic work, naming the institution and the
   advisor. Ask the advisor whether the institution has a template. *(needs you)*
3. **Establish the lawful basis under the LGPD** for the secondary use. Two plausible
   routes — anonymized data falls outside the LGPD entirely under art. 12, or legitimate
   interest under art. 7(IX) for the pseudonymized stage. Worth confirming with the
   advisor which framing the institution expects to see in the text. *(needs a decision —
   see D3)*
4. **Read-only credentials against a replica**, never production. *(needs you)*
5. **Map ERP columns to the domain schema.** ~30 minutes of work in
   `src/collection/data/sources/erp.py`; the aliases are already sketched. *(mine)*
6. **Extract, anonymize, validate.** `DATA_SOURCE=erp make pipeline`. The validation step
   already checks columns, orphan customers and inverted dates. *(mine)*
7. **Volume check.** The schedule asks for ≥ 5,000 historical documents; the feature
   pipeline needs customers with several receivables each, so I would want ≥ 500 customers
   with ≥ 5 receivables each rather than 5,000 one-off invoices. *(mine, once extracted)*

**Risk.** Step 2 is not a technical step and is the one that has slipped. Per D1 this track
no longer blocks anything: steps 1-2 stay open in the background, and if authorization
arrives before mid-October the remaining steps are about a day of work, because the
extraction and anonymization code is already written and tested.

## 4. Track B — public datasets (adopted, implemented)

None of these can answer the research question on its own. They do two things that matter:
validate that the modelling pipeline produces sane results on real human behavior, and
give the paper a benchmark reproducible by any reader, with no access to company data and
no credentials beyond a Kaggle account.

| Source | What it gives us | What it lacks | Access |
|---|---|---|---|
| [Home Credit Default Risk](https://www.kaggle.com/competitions/home-credit-default-risk) | `installments_payments` has one row per payment *and* per missed payment, with scheduled vs actual date and amount — the closest public analogue to our receivables table. Multiple credits per client, so `hist_*` features are computable | No collection contacts; consumer lending, not B2B receivables | Kaggle account; competition rules must be accepted — **license needs checking for academic republication** |
| [UCI Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing) | 41,188 phone contacts ordered by date (May 2008–Nov 2010), with channel, number of contacts in the campaign, days since last contact, and previous-campaign outcome. The best public proxy for *contact strategy* — how repeated outbound attempts affect response | It is term-deposit marketing, not collection; no receivables, no amounts owed | Open, no account, CC BY 4.0 |
| [UCI Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients) | 30,000 clients, 6 months of payment history, a clean binary default target. Cheap sanity check for the classifier and imbalance handling | One row per client, no per-invoice granularity, no contacts, Taiwan 2005 | Open, no account |

The adopted pairing: **Home Credit for the payment-behavior tasks, Bank Marketing for the
contact-strategy side**. Together they cover both halves of what the generator simulates,
from real human behavior, with the honest caveat that they come from two different
populations and cannot be joined — each supports its own claim, and the paper must not
imply a single integrated dataset.

### How they enter the codebase — implemented

Each is a `DataSource` implementation next to `SyntheticSource` and `ERPSource`, mapping
onto the same four domain tables. A source now declares which tables it covers
(`provides`), so validation accepts a legitimately empty table instead of flagging it.
Everything downstream — anonymization, EDA, features, splits, models — is untouched.

| Source | Maps to | Mapping notes |
|---|---|---|
| `HomeCreditSource` | `customers`, `invoices` | One row per scheduled installment from `installments_payments`: `DAYS_INSTALMENT` becomes the due date, `DAYS_ENTRY_PAYMENT` the payment date, `AMT_INSTALMENT` the amount. Day offsets are relative to each application, so they need anchoring to a synthetic calendar date to keep the chronological split meaningful |
| `BankMarketingSource` | `customers`, `collection_events` | One row per contact attempt: `contact` to channel, `campaign`/`previous` to attempt counts, `poutcome` to outcome. Feeds contact-strategy analysis only — it produces no receivables, so Task 1 and Task 2 do not run on it |

Two practical constraints, both worth settling before the code is written:

- **Home Credit needs a Kaggle account** and acceptance of the competition rules. The
  loader raises with copy-pasteable download instructions until the files are in
  `data/raw/home-credit/`, and the licence terms still need a read before anything derived
  from it is published in the paper.
- **Bank Marketing is CC BY 4.0** and downloads straight from UCI with no account.

**First result from the Bank Marketing run** (41,188 real contacts, `docs/eda-bank-marketing.md`):
response rate falls from 13.0% on the first attempt to 5.5% by the sixth, a 58% decline.
That is real evidence for a cooldown and an attempt cap in the rules engine, replacing what
would otherwise have been an arbitrary choice in the active module.

**One caveat on the reconstructed calendar.** Bank Marketing has no date column — only
month and day of week, with rows ordered chronologically. The timestamps are reconstructed
by walking the month sequence and assuming a year boundary whenever the month goes
backwards. The reconstruction lands exactly on the documented May 2008 - November 2010
span, but it is an inference: good for ordering and month-level seasonality, meaningless at
day-level precision, and it must be described that way in the methodology.

## 5. Track C — the synthetic generator (keep regardless)

Whatever happens with A and B, the generator stays. It is what makes the test suite run in
three seconds with no credentials, no Kaggle account and no personal data, and it is what
lets someone reproduce the pipeline from a clean clone. Its role changes from "stand-in
for real data" to "fixture", and the paper should describe it as such.

## 6. Timing

Working backwards from the schedule: consolidated results are due 19/11, and the
controlled experiment starts 12/11. Model training needs the data at least three weeks
before that to leave room for tuning, interpretability and a rerun if something is wrong.

That puts the drop-dead date for a usable ERP extraction at roughly **mid-October**. Since
D1 removed the dependency, that date is now an upgrade opportunity rather than a cliff: the
predictive module proceeds on public data starting immediately, and an extraction arriving
before mid-October gets folded in.

---

## Decision log

### D1 — What do we build the predictive module on? **Decided 2026-09-10**

**Decision: public datasets now (option b), ERP as a parallel upgrade.**

The schedule's contingency said "use a public credit-scoring base to validate the pipeline,
keeping the real base for the final model". That was written before we knew that no public
base has collection events, so the fallback covers less than it appears to.

What this means concretely:

- Home Credit + Bank Marketing carry the predictive claims of the paper.
- The synthetic environment carries the end-to-end system demonstration.
- The operational comparison against conventional collection is **reframed from a measured
  result into a documented limitation and future work**. This is the real cost of the
  decision and it needs to be written into the paper's limitations section deliberately,
  not discovered by a reader.
- The research question as written in the RFC asks about efficiency gains "in comparison
  with conventional approaches". It should be revisited with the advisor — see D6 — since
  the evidence available now supports a narrower claim than the one currently stated.

Nothing is wasted if the ERP lands later: the public-data run becomes the reproducible
benchmark that a reader without company access can rerun.

## Open decisions

### D2 — Is there a real ERP available at all, and whose is it?

I do not know what you have access to: your employer's system, a client's, a partner
company's, or none. This no longer blocks the predictive module, but it is still the
highest-value open item — it is the difference between a documented limitation and a
measured result. If it is an employer's system, the authorization in step 2 is a
conversation to start this week; it costs little and the deadline for it to pay off is
mid-October.

### D3 — Which LGPD framing does the paper use?

Lower urgency after D1, since public datasets carry no personal data of ours — but the
anonymization layer still has to be described in the methodology, and the question returns
in full if the ERP track revives.

Anonymized data under art. 12 (cleanest to defend, but the claim has to hold — our
pseudonymized keys are arguably still personal data while the salt exists) versus
legitimate interest under art. 7(IX) for the pseudonymized stage plus anonymization for
anything published. I lean toward describing both stages honestly: pseudonymization
in the pipeline, anonymization for anything leaving it. **This is a question for your
advisor, not for me** — I can implement either, and the current code already supports both
readings.

### D4 — Does the synthetic data stay in the paper?

More weight after D1: with the operational comparison downgraded, the generator is now one
of the stronger candidates for the paper's original contribution. Two defensible positions
— describe it as a methodological contribution (a reproducible environment for collection
systems, genuinely uncommon in this literature), or keep it out entirely as a test fixture
and avoid any suggestion that results came from simulated data. I lean toward the first, in
its own subsection, clearly separated from results. Your advisor may have a strong view.

### D5 — Do we commit the EDA charts to the repository?

Currently yes, in PR #1, so the article can reference them. They will be replaced wholesale
when real data arrives, and PNG diffs are noise in review. The alternative is generating
them on demand and keeping only `docs/eda.md`. Low stakes, but it is easier to change now
than after the history fills up.

### D6 — How is the research question restated? **Most urgent of the open items**

The RFC asks whether the system improves credit recovery "in comparison with conventional
approaches". D1 means that comparison will not be measured unless the ERP track revives, so
the question as written promises evidence the work will not have.

This needs a conversation with your advisor, ideally before the architecture meeting on
24/09, and the outcome should be reflected in the paper's objectives rather than left to
the limitations section alone. Three shapes it could take:

- Narrow the claim to what public data supports: predictive quality (propensity and future
  default) plus a technical evaluation of the multi-agent system — latency, cost per
  contact, RAG faithfulness — without the recovery-rate comparison.
- Keep the comparison as the goal but frame it as a designed protocol that the work
  specifies and validates in simulation, leaving execution as future work.
- Keep it and bet on the ERP arriving; only viable if D2 resolves quickly.

Even if the ERP does land, the baseline still needs numbers from the company's current
process — recovery rate, average time to resolution, cost per contact — which may live in
spreadsheets or in nobody's records at all. If they cannot be reconstructed, the comparison
becomes a before/after on the same operation rather than a controlled one: still
publishable, but it has to be stated that way from the start rather than discovered in
November.
