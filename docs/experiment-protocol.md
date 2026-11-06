# Controlled experiment: protocol

The question the schedule asks of the evaluation is whether the system collects better than
the two ways collection is done without it: a person working the overdue list, and an
automated campaign that follows rules but does not predict. This document fixes how that is
measured **before** the numbers are read. Results: `docs/experiment-results.md`.
Run: `make experiment` (about 10 minutes on a laptop, 2 threads).

## 1. What kind of experiment this is, said first

**A simulation, not a field trial.** No public dataset ties collection contacts to
receivables and their outcomes (decision D1, `docs/data-acquisition-plan.md`), and the
ERP extract that would allow a before/after comparison on real customers has not
arrived. So the arms run against a simulated book of receivables, and a simulated customer
answers each contact.

That changes what a result can claim. The experiment can show **which mechanism produces a
difference and under what conditions**, and how large the difference is *given the stated
response model*. It cannot show that a real collection operation would recover the same
amounts. Every parameter that drives the answer is listed in §3 with where it comes from,
and the two most consequential assumptions are varied in §6 rather than trusted.

## 2. Arms

All arms obey the **same rules engine** (`ContactPolicy`: permitted hours in
`America/Sao_Paulo`, opt-out, disputes, 4 attempts per receivable, 48 h cooldown per
customer, 3 contacts per customer per week, 5-day grace after a promise). The legal limits
are not a feature of the system under test: a human desk is held to them too, and crediting
the system with avoiding violations the baseline was allowed to commit would count the rules
as a result. Compliance is therefore an **invariant** (§4), not a comparison.

| Arm | Whom it contacts, in what order | Channel | Days | Cost per contact |
|---|---|---|---|---|
| **no contact** | nobody — the untreated outcome, the floor every arm is read against | — | — | — |
| **manual** (baseline 1) | overdue receivables, oldest due date first, up to a daily capacity | phone | Mon–Fri | R$ 1.40 phone + R$ 2.50 labour |
| **semi-automated** (baseline 2) | overdue receivables, oldest first, whenever the rules allow | by stage: chat ≤ 30 days late, voice after | Mon–Sat | channel cost |
| **system** | overdue receivables ranked by expected value, plus a preventive reminder 1–3 days before the due date to the top quintile of predicted default risk | by stage; reminders by chat | Mon–Sat | channel cost |

The system's expected value is the one the policy already defines
(`ContactPolicy.expected_value`): the next attempt's response rate × amount × risk, less
the channel's cost. Risk is `1 − P(paid within 30 days)` from the propensity model for an
overdue receivable and `P(> 60 days late)` from the default model for a reminder. The
rankers are the champions' families (`xgboost + scale_pos_weight` for propensity,
`random_forest + class_weight` for default) with their default settings; tuning per
replication would measure the search rather than the ranking.

**Capacity is the independent variable.** Every arm runs at 2, 4, 8 and 16 contacts per
day; the automated arms also run unlimited, their natural mode. A desk is never unlimited.
Holding capacity equal is what isolates *ordering* — the one thing prediction changes —
from volume.

## 3. The simulated world

### 3.1 Population

Each replication generates a book with the synthetic source the predictive module trains on
(`SyntheticSource`, 1,200 customers, 12,000 receivables over 30 months), so the customers
have the latent propensity, delinquency history, relative amounts and seasonality the models
were built to read.

- **Decision day** `T0` = 2026-03-02. Nothing dated on or after it is visible to the system:
  payments after it are erased, delays recomputed against it, contacts and agreements after
  it dropped (`population.freeze`).
- **Cohort**: receivables already issued on `T0` and due in the 30 days after it (≈ 390 per
  replication, ≈ 63% at risk).
- **Training**: the rankers are fitted on the labelled rows of the frozen book and score the
  cohort once, on `T0`.
- **Horizon**: 90 days from `T0` — the last receivable has 60 days past due, the default line.

### 3.2 Untreated outcome

The generator draws each receivable's payment date independently of its own contact history,
so that date is the receivable's **no-intervention outcome**. Every arm starts from it; a
contact can only bring a payment forward, never push it back (tested).

### 3.3 Response to a contact

| Parameter | Value | Source |
|---|---|---|
| Conversion on attempt *k* | 13.0%, 11.5%, 10.8%, 9.4%, 7.5%, 5.5% | Bank Marketing, 41,188 real outbound calls (`OBSERVED_RESPONSE_RATE`, `docs/eda-bank-marketing.md`) |
| Individual variation | rate × customer propensity / mean propensity, capped at 60% | assumption — varied in §6 |
| Effect of a conversion | payment on the contact day + 0–5 days (uniform), or the untreated date if earlier; a reminder converts to payment by the due date | the 5-day promise grace in `ContactPolicy` |
| Dispute | 15% of an answered contact | synthetic generator's outcome mix (`_make_events`) |
| Opt-out | 5% of an answered contact | synthetic generator's outcome mix |
| Probability a contact is answered | 0.25 + 0.5 × propensity, in [0.05, 0.9] | synthetic generator |
| Channel effect | none — chat converts like phone | assumption — varied in §6 |

Bank Marketing's outcome is a client actually subscribing after a call, so it is read here as
a contact that converts into a payment. Disputes and opt-outs are not in that data and come
from the generator.

### 3.4 Cost

Channel costs are `CHANNEL_COST` in `rules/policy.py` (chat R$ 0.12, voice R$ 1.40). The
manual arm adds R$ 2.50 of labour per call — about six minutes at R$ 25/h loaded — an
assumption stated here so it can be argued with. The automated arms' cost leaves out the
infrastructure and the LLM (the template composer is used), so their cost per contact is a
lower bound.

## 4. Metrics

Read over the **at-risk** receivables of the cohort — unpaid on their due date had nobody
acted. That denominator is the same for every arm within a replication.

| Metric | Definition |
|---|---|
| **Recovered ≤ 30 days** (primary) | at-risk amount paid within 30 days of its due date ÷ at-risk amount |
| Recovered by horizon | at-risk amount paid by the end of the horizon ÷ at-risk amount |
| Days outstanding | mean days from due date to payment, unpaid counted to the horizon (a restricted mean) |
| Contacts, cost, cost per contact | from the contact log |
| Cost per R$ 1,000 gained | cost ÷ (recovered ≤ 30 days − no contact's), × 1,000 |
| **Compliance violations** | contacts an independent audit (`experiment/audit.py`, which does not import the policy) finds outside the rules — **must be 0 in every run**, or the experiment is invalid |

The 30-day window is primary because it is where a contact's *timing* shows: the propensity
model's own target window, and the cash an operation actually waits for.

## 5. Replications and analysis

- **30 replications**, seeds 1–30. Each seed fixes the book, the models' scores and the
  random draws.
- **Common random numbers**: the uniform deciding how a contact is answered is drawn per
  (receivable, attempt) before any arm runs, so two arms making the same contact see the same
  answer. Differences between arms are then differences in decisions, not in luck.
- **Paired analysis**: for each comparison (system − semi, system − manual, semi − manual) and
  capacity, the per-seed difference; reported as the mean with a 95% percentile bootstrap
  interval (10,000 resamples) and the paired standardized effect *d_z* (mean difference ÷ its
  standard deviation).
- An interval that excludes zero is reported as a difference; one that does not is reported
  as no detectable difference, not as equivalence.

## 6. Sensitivity

The two assumptions most able to change the conclusion are varied, with everything else fixed:

- **homogeneous** — everyone converts at the population rate. Removes the assumed link between
  propensity and response, so the ranker can help only through amount and timing.
- **chat_half** — written channels convert at half the phone rate. Bank Marketing measured
  phone calls; nothing measured says chat converts as well.

## 7. Threats to validity

- **External.** The world is synthetic. The size of every effect depends on the generator and
  the response model; the direction and the conditions are the claims, not the magnitudes.
- **The response model is the result's ceiling.** If real customers respond to who they are
  rather than to when they are called, or not at all to a template, the ranking has less to
  work with than here.
- **Construct.** "Recovered" means paid within a window; a payment brought forward is
  counted the same as one created. The 30-day metric separates the two only partly.
- **The responsive agent is not modelled.** A conversion is a single draw per contact. The
  conversation that follows — answering questions, issuing a second copy, negotiating — is
  evaluated elsewhere (`docs/responsive-agent.md`, `docs/llm-responder.md`); here it is folded
  into the conversion rate, equally for every arm.
- **One score, on `T0`.** Production would rescore daily; the system arm here never learns
  from the cohort's own first weeks.
- **The manual baseline is idealised.** It obeys every rule, never errs and never tires; a
  real desk is not that consistent, so this comparison flatters the baseline, which is the
  safe direction to be wrong in.
- **Statistical.** Thirty replications of one generator measure the simulator's variance,
  not the variance between real operations.
