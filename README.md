# Vendor Invoice Anomaly Monitoring

> **This repository is a sanitized demonstration inspired by a production invoice-monitoring system I built. It uses synthetic data and contains no production code, credentials, customer data, or internal infrastructure details.**

A small pipeline that flags unusual vendor invoices — a duplicate-looking
payment, a sudden spike in amount, an unusual burst of volume in one day —
by judging each vendor against **their own history**, not one fixed
threshold applied to everyone. That's the one idea worth taking from this
repo; everything else here exists to demonstrate it.

Runs entirely on synthetic data generated at startup — no network call, no
external service, no secret to configure.

```bash
pip install -e ".[dev]"
invoice-anomaly-demo
```

## What it does

Each run simulates one monitoring cycle:

1. Generate a small synthetic dataset: vendor history plus "today's" new
   invoices (`synthetic_data.py`).
2. For every vendor with new activity today, evaluate their full ledger
   (history + today) against three detection rules.
3. Write genuinely new findings to a local SQLite table, which is also the
   **deduplication log** — the same invoice+rule pair is never alerted on
   twice, across any number of reruns.
4. Print a plain-text digest of this cycle's new findings.

## Architecture

```
synthetic_data.py         vendor history + "today's activity"
        │
        ▼
run_monitoring_cycle() ─▶ for each vendor with activity today:
        │                    ledger = history + today
        │                    evaluate_vendor_ledger(ledger, rules, new_keys=today's IDs)
        │                          │
        │                ┌─────────┼─────────┐
        │                ▼         ▼         ▼
        │          duplicate  same_day   amount_spike
        │                └─────────┼─────────┘
        │                          ▼
        │                 candidates, filtered to new_keys
        ▼
persist_candidates() ─▶ SQLite dedup guard ─▶ alerts table
        ▼
render_digest() ─▶ plain-text summary, printed to stdout
```

- **`domain.py`** — one dataclass, `Invoice`. No local mirror of "all
  invoices ever"; a vendor's ledger is just history plus whatever is new.
- **`synthetic_data.py`** — generates the demo dataset in place of a live
  source system.
- **`detection/rules/`** — one file per rule, each a pure function of
  `(vendor_ledger, config) -> candidates`. A rule never talks to the
  database and never decides what counts as "new."
- **`detection/engine.py`** — the `new_keys` eligibility filter and the
  dedup-on-write logic, kept out of the rules so every rule gets the same
  protection without reimplementing it.
- **`db/models.py`** — a single `Alert` table. Its unique constraint on
  `(vendor_id, invoice_id, rule_name)` is the entire dedup mechanism.
- **`digest.py`** / **`cli.py`** — render findings as text; run one cycle.

## The three rules

**`duplicate`** — fires when a vendor has two or more invoices for the same
amount on the same day, *unless* that vendor already repeats amounts on
more than a configured fraction (default 10%) of their other days with two
or more invoices. Single-invoice days are left out of that baseline: a
repeat can't happen on them, so counting them would let a batch biller's
quiet days dilute the rate and make their routine month-end repeats look
unusual. The rate is only trusted once there are at least
`min_baseline_days` such days (default 5); below that, or for a vendor
doing it for the first time, it alerts. A rare repeat still alerts. The day being judged is excluded from its own
baseline so it can never justify itself.

**`same_day_count`** — fires when a vendor posts more invoices in a day
than ever before, *and* that count clears a floor (default 5) — a vendor
going from 1 to 3 invoices/day set a "new high" but that alone isn't
remarkable. A vendor with heavy but predictable batching only alerts when
it genuinely exceeds their own past behavior.

**`amount_spike`** — fires when an invoice is more than a configured
percentage (default 50%) above the largest invoice that vendor billed in
the preceding N months (default 12). A trailing *time* window, rather than
a trailing *count* of invoices, stays meaningful regardless of how often a
vendor bills — "last 10 invoices" is a week for a daily biller and years
for a quarterly one, while "last 12 months" means the same thing for both.

**Why this matters:** a single fixed threshold applied to every vendor
doesn't hold up on real accounts-payable data. Same-amount-same-day is
routine for instalment or per-unit billing; a flat volume cap misreads
predictable month-end batching as a spike; a statistical outlier test
(e.g. median ± k·MAD over lifetime history) gets dragged off course by
vendors who mix small routine invoices with occasional large ones. Each
rule above replaces "is this unusual in general" with "is this unusual
*for this vendor*," building its baseline from that vendor's own data.

## Example synthetic anomalies

`synthetic_data.py` seeds five kinds of vendor so one run shows both true
positives and false-positive suppression:

| Vendor (synthetic) | Pattern | Expected outcome |
|---|---|---|
| Bluewave, Northgate, Aster, Rowan | Ordinary, stable billing | No alerts, ever |
| **Fleetline Vehicle Rentals** | Routinely bills the same amount 2-3× a day (per-vehicle billing) | `duplicate` stays **silent**, even repeating again today |
| **Harborview Facility Services** | Predictable month-end batch of 6-9 documents | `same_day_count` stays **silent** on today's 6-document batch |
| **Solace Marketing Partners** | Steady ~18,000/invoice for a year, then one at 210,000 | `amount_spike` **fires** — a genuine spike |
| **Kestrel Legal Advisory** | Never repeats an amount, then bills the same amount twice in a day | `duplicate` **fires** — genuinely new for this vendor |

### Example output

```
Monitoring cycle complete for 2026-06-30
  vendors checked: 5  alerts created: 3  alerts skipped (already seen): 0

3 alert(s) this cycle
========================================
[HIGH] Unusually large amount
  Vendor:  Solace Marketing Partners (V-400)
  Amount:  210,000
  Why:     210,000 is the largest invoice from this vendor in the past 12
           months -- 981% above their previous highest of 19,422.
  Invoice: V-400-9001  Posted: 2026-06-30
```

Fleetline and Harborview generate real activity in the same run and
correctly produce nothing.

## Design decisions

- **Rules are pure functions** — no DB session, no I/O — testable with a
  hand-built list of invoices.
- **Eligibility filtering lives in the engine, not the rules.** A rule
  always sees a vendor's complete ledger for context, but only candidates
  in `new_keys` (this cycle's genuinely new invoices) can become alerts.
- **No thresholds hardcoded in rule code** — every value lives in
  `config/rules.yaml`.
- **The dedup guard is a database constraint, not a prior check.** Each
  insert targets `ON CONFLICT (vendor_id, invoice_id, rule_name) DO
  NOTHING`, so a duplicate write is a genuine no-op rather than a
  check-then-write a concurrent writer could race.
- **Plain-language alert messages** — amount, comparison, and what to look
  at next, never raw statistical terms a reader has to interpret.

## Tests / how to run

```bash
pip install -e ".[dev]"
pytest                     # 45 tests, pure/in-memory except the SQLite dedup cases
invoice-anomaly-demo       # generates data, runs detection, prints a digest
```

Run the CLI twice against the same `--db` and compare: the second run
reports the same findings as **already seen**, not as new alerts.

```bash
invoice-anomaly-demo --seed 42          # different synthetic dataset
invoice-anomaly-demo --db /tmp/demo.db  # dedup log location
invoice-anomaly-demo --rules my.yaml    # override the packaged rules.yaml
```

Docker:

```bash
docker build -t invoice-anomaly-demo .
docker run --rm invoice-anomaly-demo
```

CI runs the test suite on every push and pull request
(`.github/workflows/test.yml`).

## Finance UI (Streamlit)

A reviewer-facing screen over the same rules — no rule logic lives in the UI.

```bash
pip install -e ".[ui]"
streamlit run app/streamlit_app.py
```

- **Sample data** — the synthetic dataset above, in daily-monitoring mode.
- **Upload your export** — CSV or Excel, one row per invoice. Column names
  are auto-detected for common layouts (SAP FBL1N labels such as `Account`,
  `DocumentNo`, `Pstng Date`, `Amount in LC`; SAP field names such as
  `LIFNR`, `BELNR`, `BUDAT`, `DMBTR`; and generic names), and can be
  remapped by hand. `;`-separated files, `dd.mm.yyyy` dates and SAP's
  trailing-minus amounts (`4,502.00-`) are handled. Rows that can't be read
  are listed, not silently dropped. `examples/sample_fbl1n_export.csv` is
  the synthetic dataset in that format.
- **Two scan modes** — *daily monitoring* (only invoices on/after a date can
  alert; the rest is history) and *audit the whole file* (every invoice is
  judged against the history around it, after a configurable warm-up per
  vendor so the first weeks of history don't all read as "first time").
- **Headline figures** — amount under review (each flagged invoice counted
  once) and potential duplicate payments (the extra copies in a duplicate
  group).
- One finding per real issue: a duplicate pair is one card, not two.
- Vendor history chart per finding, an email preview of the morning digest,
  and CSV / Excel export of all findings.
- Rule thresholds are adjustable from the sidebar for the session;
  `rules.yaml` stays the source of defaults.

The file is read in memory by the local Streamlit process only.

Code: `csv_import.py` (file → `Invoice`), `review.py` (runs the engine,
groups candidates into findings, computes totals), `app/streamlit_app.py`.

## Limitations

- Synthetic data only — representative, but hand-authored, not sourced
  from a real dataset.
- No live source integration; a real deployment needs to pull from
  wherever invoices actually live.
- No alert delivery — the digest prints to stdout, and a real system
  would need its own delivery-side deduplication for repeated scheduled
  runs.
- Three rules, not an exhaustive set.
- No concurrency — vendors are evaluated one at a time.
- Single currency, whole-number amounts — no locale-aware formatting.

## What was removed or generalized from the production system

This is a clean-room rewrite, not an extraction. Removed or generalized for
confidentiality: all live source-system integration (client code,
endpoints, authentication); all infrastructure and deployment details
(hosts, schedulers, deployment scripts); all credentials and connection
configuration; all real vendor, customer, and company data; the alert
delivery mechanism (replaced with a stdout digest); and source-system-
specific operational behavior that has no equivalent here since this demo
has no external source system to integrate with.

What carried over faithfully is the detection logic and the reasoning
behind it: the three vendor-baseline rules, the new-invoices-only
eligibility filter, and the dedup-by-unique-constraint pattern all reflect
real design decisions made while building the system this demo is based
on.
