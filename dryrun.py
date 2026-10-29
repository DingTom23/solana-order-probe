"""Dry run: discover validators, leader windows and parse a real block.

Sends nothing. Produces data/dryrun.json used by the report and by the
live measurement plan.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter
from typing import List

from blockparse import parse_transactions
from rpc import RpcClient
from validators import leader_windows, select_validators
from solana_tx import build_transfer, load_keypair, random_keypair, verify_tx

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"


def parse_block(block: dict) -> dict:
    rows = parse_transactions(block)
    return {
        "slot": block.get("parentSlot", 0) + 1,
        "blockhash": block.get("blockhash"),
        "block_time": block.get("blockTime"),
        "block_height": block.get("blockHeight"),
        "tx_count": len(rows),
        "rows": rows,
    }

def summarize(parsed: dict) -> dict:
    rows = parsed["rows"]
    non_vote = [r for r in rows if not r["vote"]]
    votes = [r for r in rows if r["vote"]]
    fees = [r["fee"] for r in rows if r["fee"] is not None]
    priorities = [r["priority_micro_lamports"] for r in rows if r["priority_micro_lamports"] is not None]
    first_non_vote = min((r["index"] for r in non_vote), default=None)
    last_vote = max((r["index"] for r in votes), default=None)
    return {
        "tx_count": len(rows),
        "vote_count": len(votes),
        "non_vote_count": len(non_vote),
        "first_non_vote_index": first_non_vote,
        "last_vote_index": last_vote,
        "votes_before_non_votes": bool(votes and non_vote and last_vote < first_non_vote),
        "fee_min": min(fees) if fees else None,
        "fee_median": statistics.median(fees) if fees else None,
        "fee_max": max(fees) if fees else None,
        "priority_seen": len(priorities),
        "priority_unique": len(set(priorities)),
        "priority_top": Counter(priorities).most_common(5),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default=os.environ.get("SOLANA_RPC", DEFAULT_RPC))
    ap.add_argument("--proxy", default=os.environ.get("RPC_PROXY"))
    ap.add_argument("--out", default="data/dryrun.json")
    ap.add_argument("--validators", type=int, default=8)
    ap.add_argument("--leader-lookahead", type=int, default=1200)
    ap.add_argument("--block-lag", type=int, default=40)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    rpc = RpcClient(args.rpc, proxy=args.proxy)

    version = rpc.get_version()
    epoch = rpc.get_epoch_info()
    nodes = rpc.get_cluster_nodes()
    votes = rpc.get_vote_accounts()
    selected = select_validators(nodes, votes, wanted=args.validators)

    slot = rpc.get_slot("confirmed")
    leaders = rpc.get_slot_leaders(slot + 1, args.leader_lookahead)
    windows = leader_windows(leaders, [v["node_pubkey"] for v in selected], slot + 1)

    # Build (but never send) one transaction per validator to prove the signer.
    payer = load_keypair(os.environ.get("PAYER_KEYPAIR", "wallet/solana.json")) if os.path.exists(
        os.environ.get("PAYER_KEYPAIR", "wallet/solana.json")
    ) else random_keypair()
    destination = random_keypair()
    blockhash = rpc.get_latest_blockhash()[0]
    samples = []
    for i, validator in enumerate(selected):
        lamports = 1000 + i
        tx, msg, sig = build_transfer(
            payer, destination.address, lamports, blockhash, cu_price_micro_lamports=1000
        )
        assert verify_tx(payer, tx, msg)
        samples.append(
            {
                "validator": validator["node_pubkey"],
                "lamports": lamports,
                "signature": sig,
                "tx_bytes": len(tx),
                "next_slots": windows.get(validator["node_pubkey"], [])[:5],
            }
        )

    # Parse a recent block to validate the block analyser against live data.
    block_slot = max(1, slot - args.block_lag)
    block = rpc.get_block(block_slot)
    parsed = parse_block(block) if block else None
    summary = summarize(parsed) if parsed else None

    out = {
        "rpc": args.rpc,
        "cluster_version": version,
        "epoch": epoch,
        "current_slot": slot,
        "validators": selected,
        "families": sorted({v["family"] for v in selected}),
        "leader_lookahead": args.leader_lookahead,
        "leader_windows": {k: v[:20] for k, v in windows.items()},
        "payer": payer.address,
        "destination": destination.address,
        "samples": samples,
        "sample_block_slot": block_slot,
        "sample_block_summary": summary,
        "sample_block": parsed,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    print(f"cluster {version['solana-core']} epoch {epoch['epoch']} slot {slot}")
    print(f"validators selected: {len(selected)} families: {sorted({v['family'] for v in selected})}")
    for v in selected:
        nxt = windows.get(v["node_pubkey"], [])[:3]
        print(f"  {v['version']:>12} {v['family']:<16} stake={v['stake_sol']:>12,.0f} SOL next={nxt}")
    if summary:
        print(
            "sample block",
            block_slot,
            "txs",
            summary["tx_count"],
            "votes",
            summary["vote_count"],
            "votes_first",
            summary["votes_before_non_votes"],
        )
    print("wrote", args.out)


if __name__ == "__main__":
    main()
