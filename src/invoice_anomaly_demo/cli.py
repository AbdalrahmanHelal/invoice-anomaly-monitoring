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
from importlib import resources

import yaml

from invoice_anomaly_demo.db.engine import make_session_factory, session_scope
from invoice_anomaly_demo.detection.engine import run_monitoring_cycle
from invoice_anomaly_demo.digest import render_digest
from invoice_anomaly_demo.synthetic_data import generate_demo_dataset


def _load_rules_config(rules_path: str | None) -> dict:
    """Load rules.yaml from an explicit path, or fall back to the copy
    shipped inside the package -- so the default works from any working
    directory and any install method, not just `pip install -e` from the
    repo root."""
    if rules_path is not None:
        return yaml.safe_load(open(rules_path, encoding="utf-8"))

    packaged = resources.files("invoice_anomaly_demo").joinpath("config/rules.yaml")
    return yaml.safe_load(packaged.read_text(encoding="utf-8"))


def run(rules_path: str | None = None, db_path: str = "invoice_anomaly_demo.db", seed: int = 7) -> None:
    rules_config = _load_rules_config(rules_path)
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
    parser.add_argument("--rules", default=None, help="Path to a custom rules.yaml (defaults to the packaged config)")
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
