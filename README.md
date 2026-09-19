# Invoice Anomaly Detection — Sanitized Demo

> **This repository is a sanitized demonstration inspired by a production invoice-monitoring system I built. It uses synthetic data and contains no production code, credentials, customer data, or internal infrastructure details.**

A small, self-contained pipeline that flags unusual vendor invoices — a
duplicate-looking payment, a sudden spike in amount, an unusual burst of
volume in one day — using each vendor's *own* history as the yardstick,
rather than one fixed threshold applied to everyone.

Everything here runs on synthetic data generated at startup. There is no
network call, no external service, and no configuration secret required to
run it.

```bash
pip install -e ".[dev]"
invoice-anomaly-demo
```

That's it — it generates a small synthetic dataset, runs detection, and
prints a plain-text digest of what it found.

---

## What this system does

Each run simulates one monitoring cycle:

1. A set of synthetic vendors and their invoice history is generated
   (`synthetic_data.py`) — some vendors bill normally, some have quirky but
   entirely legitimate billing patterns, and a couple have a genuinely
   anomalous invoice seeded in on purpose.
2. For every vendor with new invoice activity "today", their full ledger
   (history + today) is evaluated against three detection rules.
3. Genuinely new findings are written to a local SQLite table, which
   double as a **deduplication log** — the same invoice+rule pair is never
   alerted on twice, even across repeated runs.
4. A human-readable digest of this cycle's *new* findings is printed.

The interesting part isn't the plumbing — it's that all three rules judge
a vendor against **their own history**, not a single number applied to
every vendor in the system. That distinction is the entire point of this
demo, and the rest of this README explains why it matters.

---

## Architecture

```
synthetic_data.py                 vendor history + "today's activity"
        │
        ▼
run_monitoring_cycle()  ──▶  for each vendor with activity today:
        │                        ledger = history + today
        │                        evaluate_vendor_ledger(ledger, rules, new_keys=today's invoice IDs)
        │                              │
        │                    ┌─────────┼─────────┐
        │                    ▼         ▼         ▼
        │              duplicate  same_day   amount_spike
        │                    │         │         │
        │                    └─────────┼─────────┘
        │                              ▼
        │                     candidates, filtered to new_keys
        ▼
persist_candidates()  ──▶  SQLite dedup guard ──▶ alerts table
        │
        ▼
render_digest()  ──▶  plain-text summary, printed to stdout
```

- **`domain.py`** — one dataclass, `Invoice`. No local mirror of "all
  invoices ever" — a vendor's ledger is just history plus whatever is new.
- **`synthetic_data.py`** — generates the demo dataset. Stands in for a
  live source system. See [Example synthetic anomalies](#example-synthetic-anomalies)
  below for exactly what it seeds and why.
- **`detection/rules/`** — one file per rule, each a small, independent
  function of `(vendor_ledger, config) -> candidates`. A rule never talks
  to the database and never knows what "new" means — see below.
- **`detection/engine.py`** — the eligibility filter (`new_keys`) and the
  SQLite dedup guard. This is where "only new invoices are eligible" is
  actually enforced, deliberately kept separate from rule logic.
- **`db/models.py`** — a single `Alert` table with a unique constraint on
  `(vendor_id, invoice_id, rule_name)`. That constraint *is* the
  deduplication mechanism — no separate "have I seen this" check, just a
  write that silently no-ops on conflict.
- **`digest.py`** — turns a list of alerts into readable text. No template
  engine, no email — just a function that returns a string.
- **`cli.py`** — one command. Generates data, runs a cycle, prints the
  digest.

---

## Why vendor-specific baselines matter

This is the central design decision in the whole project, and it comes
from a real, hard lesson: **a single fixed threshold, applied to every
vendor, does not work on real accounts-payable data.**

Concretely:

- **Duplicate detection.** "Same vendor, same amount, same day" sounds
  like an obvious duplicate. It is also exactly how a lot of legitimate
  billing works — instalment plans, or a vendor invoicing per unit /
  per vehicle where several units happen to cost the same. A vendor doing
  this routinely will trip a naive rule on a large fraction of their
  billing days. Once that happens, the alert stream becomes noise, and
  noisy alerts get ignored — including the rare, genuine case sitting
  right next to it.
- **Same-day volume.** "More than N invoices in a day" looks like a spike
  — until you notice a vendor does predictable month-end batch
  reconciliation every month, and N is comfortably inside their normal
  batch size. A flat cap flags their normal cycle as new, forever.
- **Amount spikes.** A statistical outlier test (e.g. median + k·MAD over
  a vendor's lifetime history) sounds principled, but many real vendors
  mix small routine invoices with occasional large legitimate ones. That
  drags the median down, and a huge share of *entirely ordinary* invoices
  end up "above threshold."

The fix in every case is the same shape: **stop asking "is this unusual in
general" and start asking "is this unusual for this specific vendor."**
Each rule below builds its own notion of "normal" from that vendor's own
history before deciding whether today's invoice deserves attention.

---

## The three rules

### `duplicate` — same amount, same day, judged against the vendor's own habit

Fires when a vendor has two or more invoices for the same amount on the
same day — **unless** that vendor already does this routinely. Each
repeated-amount day is compared against how often the *same vendor*
repeats an amount on their *other* billing days:

- Never happened before → alert. This is genuinely new for them.
- Happens, but rarely → still alert. Rare-but-not-unprecedented is exactly
  what's worth a second look.
- Happens on more than a configured fraction of their other billing days
  → stay quiet. That's just how they invoice.

The day being judged is explicitly excluded from its own baseline, so a
vendor can never use the flagged day itself as evidence that it's normal.

### `same_day_count` — a new personal record for daily volume

Fires when a vendor posts more invoices in one day than they ever have
before, **and** that count clears a floor (so a vendor going from 1 to 3
invoices/day, while technically a "new high," doesn't get flagged for
something that small). A vendor with predictable heavy batching keeps
setting *new* highs only when they genuinely exceed their own past
behavior — not every time they repeat their normal cycle.

### `amount_spike` — a new high over a trailing time window

Fires when an invoice is more than a configured percentage above the
**largest invoice that vendor has billed in the preceding N months**
(12 by default) — a meaningful new high, not just "bigger than usual."

A trailing *time* window, rather than a trailing *count* of invoices, is
what makes this stable across vendors with very different billing
frequencies: "the last 10 invoices" means a week for a vendor who bills
daily and years for one who bills quarterly, so the same number means
something different for every vendor. A trailing year means the same
thing for all of them, and matches the actual question a reviewer asks:
*"has this vendor ever billed this much recently?"*

---

## Example synthetic anomalies

`synthetic_data.py` seeds five kinds of vendor on purpose so a single run
demonstrates both true positives and the false-positive suppression that
motivated the rules above:

| Vendor (synthetic)            | Pattern                                      | Expected outcome |
|--------------------------------|-----------------------------------------------|------------------|
| Bluewave, Northgate, Aster, Rowan | Ordinary, stable billing                   | No alerts, ever |
| **Fleetline Vehicle Rentals**   | Routinely bills the same amount 2-3× on a day (per-vehicle billing) | `duplicate` stays **silent** even though it repeats again today |
| **Harborview Facility Services**| Predictable month-end batch of 6-9 documents  | `same_day_count` stays **silent** on today's 6-document batch — well inside their normal range |
| **Solace Marketing Partners**   | Steady ~18,000/invoice for over a year, then one invoice at 210,000 | `amount_spike` **fires** — a genuine, first-time spike |
| **Kestrel Legal Advisory**      | Steady billing, never repeats an amount, then bills the same amount twice in one day | `duplicate` **fires** — genuinely new behavior for this vendor |

Run the CLI and you'll see exactly three alerts: the Solace spike and both
Kestrel invoices. Fleetline and Harborview generate real activity in the
same run and correctly produce nothing.

---

## Design decisions

- **Vendor-specific baselines over global thresholds**, throughout — see
  above. This is the one idea worth taking from this repo.
- **Rules are pure functions.** `rule.run(ledger, config) -> candidates`
  takes no database session and performs no I/O. Every rule is testable
  with a hand-built list of invoices and no fixtures beyond that.
- **Eligibility filtering lives in the engine, not the rules.** A rule
  always sees a vendor's *complete* ledger (it needs the full picture to
  know what's normal), but only candidates whose invoice ID is in
  `new_keys` — this run's genuinely new activity — are allowed to become
  alerts. This is deliberately a separate step, not baked into each rule,
  so every rule automatically gets the same first-touch-flood protection
  without reimplementing it three times.
- **No config values hardcoded in rule code.** Every threshold, rate, and
  window lives in `config/rules.yaml`. Tuning a rule is a config change,
  not a code change.
- **The database is a dedup log, not a data mirror.** The only table is
  `alerts`, and its unique constraint on `(vendor_id, invoice_id,
  rule_name)` is the entire deduplication mechanism — there's no separate
  "already alerted?" query, just an insert that's a no-op on conflict.
- **Plain-language alert messages.** Every message states the amount, the
  comparison it failed, and what to look at next — never raw statistical
  terms like "median" or "k·MAD." A message a reader has to interpret
  before they can act on it is a message that gets ignored.

---

## False-positive handling

This is worth calling out on its own, because it's the part that
distinguishes a demo from a working system: **every rule here was shaped
by a false positive that had to be fixed**, not designed from a textbook
outlier-detection formula. The [Example synthetic anomalies](#example-synthetic-anomalies)
table above encodes exactly that — two vendors (Fleetline, Harborview)
exist specifically to prove the suppression logic holds, not just the
detection logic.

The general pattern across all three rules:

1. Start from "what would a naive, single-threshold rule do?"
2. Identify the realistic vendor behavior that naive rule would
   misclassify.
3. Replace the fixed threshold with a comparison against that specific
   vendor's own recent behavior.
4. Keep a floor or cold-start guard so "vendor-specific" doesn't mean
   "alerts on a single ordinary transaction because it happens to be a
   vendor's very first invoice."

---

## Limitations

This is a demo, not a production system, and it's worth being explicit
about what that means:

- **Synthetic data only.** The patterns are representative of what shows
  up in real accounts-payable data, but they are hand-authored, not
  sourced from any real dataset.
- **No live source integration.** A real deployment needs to pull from
  wherever invoices actually live (an ERP, an accounting API, a data
  warehouse export) — that integration layer is intentionally not part of
  this repo (see below).
- **No alert delivery.** The digest prints to stdout. A real system needs
  a delivery mechanism (email, Slack, a ticketing system) and — just as
  importantly — its own deduplication, so a scheduled job running every
  few minutes doesn't repeat the same notification on every run.
- **Three rules, not an exhaustive rule set.** These three generalize well
  and were the ones worth demonstrating; a production system would likely
  have more, and would keep applying the same "check it against real data
  before trusting it" discipline to each new one.
- **No concurrency.** Vendors are evaluated one at a time. A real system
  handling many vendors per cycle would want to parallelize the
  independent per-vendor work.
- **Single currency, whole-number amounts in the digest.** Real deployments
  need proper currency/locale-aware parsing and formatting; this demo
  intentionally keeps that out of scope.

---

## How to run it

### Locally

```bash
pip install -e ".[dev]"
invoice-anomaly-demo
```

Optional flags:

```bash
invoice-anomaly-demo --seed 42          # different synthetic dataset
invoice-anomaly-demo --db /tmp/demo.db  # dedup log location
```

Run it twice in a row and compare: the second run reports the same
alerts as **already seen**, not as new ones — that's the SQLite dedup
guard working as intended.

### Tests

```bash
pytest
```

25 tests, all pure/in-memory except the handful that exercise the SQLite
dedup guard (each gets a fresh temp database).

### Docker

```bash
docker build -t invoice-anomaly-demo .
docker run --rm invoice-anomaly-demo
```

---

## What was removed or generalized from the production system

This repository is a clean-room rewrite, not an extraction. Everything
below was deliberately left out or replaced, for confidentiality:

- **All source-system integration.** The production system's client for
  its live ERP/accounting source — endpoint paths, authentication,
  request shapes, rate limits, and several source-specific quirks it had
  to work around — is not reproduced here at all. This demo starts from
  in-memory synthetic data instead of any network call.
- **All infrastructure and deployment details.** Hostnames, server
  addresses, scheduler configuration, process-manager unit files, and
  deployment scripts are not included. This demo ships a single
  `Dockerfile` for local use only.
- **All credentials and connection configuration.** No `.env` file,
  connection string, API key, or secret of any kind exists in this
  repository, real or placeholder-shaped.
- **All real vendor, customer, and company data.** Every vendor name,
  amount, and invoice ID in `synthetic_data.py` is fabricated. No real
  business relationship, transaction, or identifier appears anywhere.
- **All alert delivery mechanics.** The production system's email/notification
  delivery, recipient configuration, and message templating are not
  reproduced. This demo prints a digest to stdout instead.
- **Several source-system-specific edge cases.** The production system
  dealt with a handful of quirks specific to its particular source
  system (inconsistent field casing between two endpoints, a timezone
  mismatch between the source system's clock and the scheduler, a
  reverse-proxy filter that rejected certain query syntax, and a gap
  between two endpoints where one returned a document range the other
  didn't). None of that is source-system-agnostic or demo-relevant, so
  none of it is reproduced here — the demo has no external source system
  to have quirks in.
- **Operational tooling.** Structured JSON logging, scheduled-job
  configuration, and monitoring/alerting-on-the-monitor ("is the
  pipeline itself still working") are real operational concerns in the
  production system that are out of scope for a small, readable demo.

What **was** carried over faithfully is the detection logic itself and
the reasoning behind it — the three rules, the vendor-specific-baseline
approach, the new-invoices-only eligibility filter, and the SQLite
dedup-by-unique-constraint pattern all reflect real design decisions made
(and, in a couple of cases, corrected after a bad first attempt) while
building the system this demo is based on.
