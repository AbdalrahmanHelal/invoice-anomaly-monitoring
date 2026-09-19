"""Single CLI command: run one monitoring cycle end to end.

Generates the synthetic dataset, evaluates every vendor with activity
"today" against their full ledger, persists genuinely new alerts to a local
SQLite dedup log, and prints a human-readable digest -- one flattened
pipeline, since the demo has nothing that benefits from being split into
separate detect/alert steps the way a real scheduled job would be.
"""
from __future__ import annotations

import argparse
import sys

import yaml

from invoice_anomaly_demo.db.engine import make_session_factory, session_scope
from invoice_anomaly_demo.detection.engine import run_monitoring_cycle
from invoice_anomaly_demo.digest import render_digest
from invoice_anomaly_demo.synthetic_data import generate_demo_dataset


def run(rules_path: str = "config/rules.yaml", db_path: str = "invoice_anomaly_demo.db", seed: int = 7) -> None:
    rules_config = yaml.safe_load(open(rules_path, encoding="utf-8"))
    dataset = generate_demo_dataset(seed=seed)

    session_factory = make_session_factory(db_path)
    with session_scope(session_factory) as session:
        result = run_monitoring_cycle(
            session,
            rules_config,
            history_by_vendor=dataset.history,
            todays_activity_by_vendor=dataset.todays_activity,
        )

    print(f"Monitoring cycle complete for {dataset.today}")
    print(
        f"  vendors checked: {result.vendors_checked}  "
        f"alerts created: {result.alerts_created}  "
        f"alerts skipped (already seen): {result.alerts_skipped}\n"
    )
    print(render_digest(result.candidates))


def main() -> None:
    parser = argparse.ArgumentParser(prog="invoice-anomaly-demo")
    parser.add_argument("--rules", default="config/rules.yaml", help="Path to rules.yaml")
    parser.add_argument("--db", default="invoice_anomaly_demo.db", help="SQLite dedup log path")
    parser.add_argument("--seed", type=int, default=7, help="Synthetic data random seed")
    args = parser.parse_args()

    try:
        run(rules_path=args.rules, db_path=args.db, seed=args.seed)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
