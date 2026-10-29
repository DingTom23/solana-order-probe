"""Score measured blocks: fee priority vs arrival order vs neither."""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple


def kendall_tau(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[i] - xs[j]
            dy = ys[i] - ys[j]
            if dx == 0 or dy == 0:
                continue
            if (dx > 0) == (dy > 0):
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total


def load_sends(path: str) -> Dict[str, dict]:
    out = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            out[row["signature"]] = row
    return out


def block_positions(block: dict) -> Dict[str, int]:
    positions = {}
    for index, item in enumerate(block.get("transactions", [])):
        sigs = item.get("transaction", {}).get("signatures") or []
        if sigs:
            positions[sigs[0]] = index
    return positions


def analyze_block(block: dict, sends: Dict[str, dict]) -> Optional[dict]:
    positions = block_positions(block)
    matched = []
    for sig, pos in positions.items():
        if sig in sends:
            row = dict(sends[sig])
            row["position"] = pos
            matched.append(row)
    if len(matched) < 3:
        return None
    matched.sort(key=lambda r: r["position"])
    fees = [r["fee_micro_lamports"] for r in matched]
    seqs = [r["send_seq"] for r in matched]
    pos = [r["position"] for r in matched]
    tau_fee = kendall_tau(pos, fees)
    tau_seq = kendall_tau(pos, seqs)
    return {
        "slot": matched[0]["slot"],
        "validator": matched[0]["validator"],
        "n_landed": len(matched),
        "n_burst": None,
        "positions": pos,
        "fees": fees,
        "send_seq": seqs,
        "tau_pos_fee": tau_fee,
        "tau_pos_seq": tau_seq,
        "fee_priority_score": None if tau_fee is None else -tau_fee,
        "arrival_score": None if tau_seq is None else -tau_seq,
        "fee_desc_exact": all(fees[i] >= fees[i + 1] for i in range(len(fees) - 1)),
        "seq_asc_exact": all(seqs[i] <= seqs[i + 1] for i in range(len(seqs) - 1)),
    }


def mean(values: List[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(blocks: List[dict]) -> dict:
    per_validator = defaultdict(list)
    for block in blocks:
        per_validator[block["validator"]].append(block)
    out = {}
    for validator, rows in per_validator.items():
        fee_scores = [r["fee_priority_score"] for r in rows]
        arrival_scores = [r["arrival_score"] for r in rows]
        out[validator] = {
            "blocks": len(rows),
            "landed": sum(r["n_landed"] for r in rows),
            "mean_fee_priority_score": mean(fee_scores),
            "mean_arrival_score": mean(arrival_scores),
            "fee_desc_exact_ratio": sum(1 for r in rows if r["fee_desc_exact"]) / len(rows),
            "seq_asc_exact_ratio": sum(1 for r in rows if r["seq_asc_exact"]) / len(rows),
            "rows": rows,
        }
    return out


def classify(stats: dict) -> str:
    fee = stats["mean_fee_priority_score"]
    arrival = stats["mean_arrival_score"]
    if fee is None or arrival is None:
        return "insufficient-data"
    if fee > 0.4 and fee > arrival + 0.15:
        return "fee-priority"
    if arrival > 0.4 and arrival > fee + 0.15:
        return "arrival-order"
    if fee > 0.15 and arrival > 0.15:
        return "mixed"
    return "no-clear-rule"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="data/analysis.json")
    args = ap.parse_args()

    sends = load_sends(os.path.join(args.data, "sends.jsonl"))
    blocks = []
    for path in sorted(glob.glob(os.path.join(args.data, "blocks", "*.json"))):
        with open(path, "r", encoding="utf-8") as fh:
            block = json.load(fh)
        result = analyze_block(block, sends)
        if result:
            blocks.append(result)

    stats = summarize(blocks)
    analysis = {
        "blocks_with_signal": len(blocks),
        "sends_logged": len(sends),
        "validators": stats,
        "classification": {v: classify(s) for v, s in stats.items()},
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, indent=1)

    print(f"blocks with >=3 matched txs: {len(blocks)}")
    for validator, s in sorted(stats.items(), key=lambda kv: -kv[1]["blocks"]):
        print(
            f"{validator[:12]} blocks={s['blocks']:>2} landed={s['landed']:>3} "
            f"fee_score={s['mean_fee_priority_score'] if s['mean_fee_priority_score'] is not None else float('nan'):+.2f} "
            f"arrival_score={s['mean_arrival_score'] if s['mean_arrival_score'] is not None else float('nan'):+.2f} "
            f"-> {classify(s)}"
        )
    print("wrote", args.out)


if __name__ == "__main__":
    main()
