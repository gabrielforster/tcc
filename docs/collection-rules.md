# Collection rules

Whether a given outbound contact may happen, and why not when it may not.

Every rule is either **legal** or **empirical**, and the distinction is load-bearing: a
legal limit is not ours to tune, while an empirical one is a parameter the controlled
experiment should vary.

## Legal constraints

Grounded in the CDC (art. 42 — collection must not expose, threaten or harass), the 2021
*superendividamento* amendments, and the LGPD right to object.

| Rule | Behaviour |
|---|---|
| Permitted hours | Weekdays 08:00–20:00, Saturdays 08:00–14:00, never Sundays or holidays |
| Opt-out | Absolute for outbound contact; checked in the orchestrator before any agent runs |
| Disputed debt | Leaves the campaign and goes to a human |
| Frequency ceiling | At most 3 contacts per customer per week — repeated contact is the form harassment takes in practice |

**Permitted hours are wall-clock local time**, evaluated in `America/Sao_Paulo`. This is
not a detail: evaluating in UTC would have permitted contact at 05:00 local, which the demo
surfaced and which is exactly the kind of bug that would survive into production unnoticed.

Holidays are injected rather than hard-coded — the relevant set is municipal as well as
national, and baking a list into the code would be false precision.

## Empirical constraints

Grounded in the Bank Marketing data (`docs/eda-bank-marketing.md`): across 41,188 real
outbound attempts, the response rate falls from 13.0% on the first attempt to 5.5% by the
sixth — a 58% decline.

| attempt | response rate |
|---|---|
| 1 | 13.0% |
| 2 | 11.5% |
| 3 | 10.8% |
| 4 | 9.4% |
| 5 | 7.5% |
| 6 | 5.5% |

Two defaults follow:

- **`max_attempts = 4`.** By the fifth attempt the response rate has roughly halved, so
  further attempts mostly generate cost and irritation rather than recoveries.
- **`cooldown_hours = 48`.** Attempts are spread rather than stacked.

Both are defaults, not constants. They are precisely the knobs the experiment varies, and
the number justifying them is reproducible from a public dataset rather than asserted.

A promise to pay pauses contact for 5 days: chasing someone who has just committed to a
date is the fastest way to lose the commitment.

## Expected value

`ContactPolicy.expected_value(attempt, channel, amount)` combines the measured decay with
the channel's cost:

| channel | cost per contact |
|---|---|
| email | R$ 0.01 |
| sms | R$ 0.09 |
| whatsapp | R$ 0.12 |
| phone | R$ 1.40 |

It is not a precise forecast. It exists so "is another attempt worth it" is answered with a
number rather than a hunch, and so the cost side of the operational comparison has a basis.

## Evaluation order

Legal blocks are evaluated first, so an opted-out customer is refused *for being opted out*
rather than for an attempt count. The reason travels with the decision, and the proactive
agent turns it into a `suppressed` event — a customer who is never contacted still leaves a
record explaining why, which is what makes these rules auditable rather than merely present.

## Channel by delinquency stage

| Stage | Channel |
|---|---|
| pre-due, 1–7, 8–30 days | whatsapp |
| 31–60, 60+ days | phone |

Cheap and unobtrusive first; costly and direct only once the debt has aged.
