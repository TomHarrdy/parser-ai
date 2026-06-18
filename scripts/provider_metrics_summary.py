#!/usr/bin/env python3
"""Summarize provider effectiveness JSONL snapshots."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


FIELDS = (
    "collected",
    "freshness_verified",
    "freshness_skipped",
    "dedup_new",
    "dedup_skipped",
    "analyzed_relevant",
    "analysis_skipped",
    "saved",
    "notified",
    "errors",
)


def _read_tail(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:] if limit > 0 else rows


def summarize(path: Path, limit: int) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {field: 0 for field in FIELDS})
    for row in _read_tail(path, limit):
        for provider, metrics in (row.get("providers") or {}).items():
            for field in FIELDS:
                totals[provider][field] += int(metrics.get(field) or 0)
    return dict(sorted(totals.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        default="data/provider_effectiveness.jsonl",
        help="Provider metrics JSONL path",
    )
    parser.add_argument("--last", type=int, default=50, help="Number of latest runs to summarize")
    args = parser.parse_args()

    totals = summarize(Path(args.path), args.last)
    if not totals:
        print("No provider metrics found.")
        return

    header = ["provider", *FIELDS, "save_rate", "alert_rate"]
    print("\t".join(header))
    for provider, metrics in totals.items():
        collected = metrics["collected"]
        saved = metrics["saved"]
        notified = metrics["notified"]
        save_rate = f"{saved / collected:.2%}" if collected else "0.00%"
        alert_rate = f"{notified / collected:.2%}" if collected else "0.00%"
        row = [provider, *(str(metrics[field]) for field in FIELDS), save_rate, alert_rate]
        print("\t".join(row))


if __name__ == "__main__":
    main()
