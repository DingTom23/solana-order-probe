"""Pilot: score the ordering of *all* transactions in recent blocks.

This is methodology validation, not the bounty measurement: the bounty
requires evidence from transactions we send ourselves. The pilot shows that
the parser and the fee/position statistics behave sensibly on live blocks.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import Counter

from analyze import kendall_tau
from blockparse import parse_transactions
from rpc import RpcClient


def score_block(block: dict) -> dict:
    rows = parse_transactions(block)
    votes = sum(1 for row in rows if row["vote"])
    with_fee = [row for row in rows if not row["vote"] and row["priority_micro_lamports"] is not None]
    positions = [row["index"] for row in with_fee]
    fees = [row["priority_micro_lamports"] for row in with_fee]
    tau = kendall_tau(positions, fees)
    exact = all(fees[i] >= fees[i + 1] for i in range(len(fees) - 1)) if len(fees) > 1 else None
    return {
        "tx_count": len(rows),
        "vote_count": votes,
        "non_vote_with_priority": len(with_fee),
        "tau_pos_fee": tau,
        "fee_priority_score": None if tau is None else -tau,
        "fee_desc_exact": exact,
        "top_fees": Counter(fees).most_common(3),
        "median_fee": statistics.median(fees) if fees else None,
        "version_counts": dict(Counter(row["version"] for row in rows)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default=os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com"))
    ap.add_argument("--proxy", default=os.environ.get("RPC_PROXY"))
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--lag", type=int, default=60)
    ap.add_argument("--out", default="data/pilot.json")
    args = ap.parse_args()

    rpc = RpcClient(args.rpc, proxy=args.proxy)
    slot = rpc.get_slot("confirmed")
    epoch = rpc.get_epoch_info()
    epoch_start = epoch["absoluteSlot"] - epoch["slotIndex"]
    schedule = rpc.get_leader_schedule() or {}
    leader_by_slot = {}
    for node, indexes in schedule.items():
        for idx in indexes:
            leader_by_slot[epoch_start + idx] = node

    results = []
    for i in range(args.blocks):
        target = slot - args.lag - i * 3
        block = None
        for attempt in range(4):
            block = rpc.get_block(target)
            if block:
                break
            time.sleep(1.0)
        if not block:
            print("no block", target)
            continue
        stats = score_block(block)
        stats["slot"] = target
        stats["leader"] = leader_by_slot.get(target)
        results.append(stats)
        print(
            f"slot {target} leader {(stats['leader'] or '?')[:8]} "
            f"txs {stats['tx_count']} votes {stats['vote_count']} "
            f"priority_txs {stats['non_vote_with_priority']} "
            f"fee_score {stats['fee_priority_score'] if stats['fee_priority_score'] is not None else float('nan'):+.2f} "
            f"exact_desc {stats['fee_desc_exact']} versions {stats['version_counts']}"
        )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"slot_at_start": slot, "blocks": results}, fh, indent=1)
    if results:
        scores = [r["fee_priority_score"] for r in results if r["fee_priority_score"] is not None]
        print("mean fee score over pilot blocks:", round(sum(scores) / len(scores), 3) if scores else None)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
