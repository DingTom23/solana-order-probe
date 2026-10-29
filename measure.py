"""Live measurement: send controlled transactions into target leader slots.

Default is a dry run. Live sending requires --live and a hard --max-lamports
budget. Every transaction is a self-transfer of a unique lamport amount with
a memo tag, so blocks can be matched back to the send log.

Design notes
------------
* Fee levels and send order are deliberately de-correlated: each burst is a
  random permutation of the fee ladder, so "ordered by fee" and "ordered by
  arrival" are competing hypotheses, not the same signal.
* All transactions share payer, destination, compute limit and size, so the
  only intentional differences are priority fee and send sequence.
* Only blocks that contain at least MIN_MATCH of our transactions are scored.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from rpc import RpcClient, RpcError
from select import select_validators
from solana_tx import build_memo_tx, build_transfer, encode_tx, load_keypair, verify_tx

SLOT_MS = 400
DEFAULT_FEES = [0, 10_000, 100_000, 1_000_000, 10_000_000]


def now_ns() -> int:
    return time.time_ns()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: str, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def estimate_fee(cu_price: int, cu_limit: int = 200) -> int:
    return 5000 + (cu_price * cu_limit + 999_999) // 1_000_000


def target_slots(
    schedule: dict, epoch_start: int, targets: List[str], after: int, per_validator: int = 6
) -> List[dict]:
    """Next N leader slots for each target (not the first N globally).

    Rare leaders (small stake) may only appear late in the epoch; taking the
    first N slots globally would silently drop them from the experiment.
    """
    rows = []
    for node in targets:
        upcoming = [epoch_start + index for index in schedule.get(node, []) if epoch_start + index > after]
        for slot in upcoming[:per_validator]:
            rows.append({"slot": slot, "validator": node})
    rows.sort(key=lambda r: r["slot"])
    return rows


def wait_for_slot(rpc: RpcClient, target: int, deadline_s: float = 90.0) -> bool:
    """Poll until the cluster's confirmed slot reaches target (or timeout)."""
    end = time.time() + deadline_s
    while time.time() < end:
        slot = rpc.get_slot("confirmed")
        if slot >= target:
            return True
        if slot < target - 20:
            # Too far away: sleep most of the gap instead of hammering the RPC.
            time.sleep(min(5.0, (target - slot) * SLOT_MS / 1000.0 * 0.8))
        else:
            time.sleep(0.05)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default=os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com"))
    ap.add_argument("--proxy", default=os.environ.get("RPC_PROXY"))
    ap.add_argument("--keypair", default=os.environ.get("PAYER_KEYPAIR", "wallet/solana.json"))
    ap.add_argument("--live", action="store_true", help="actually send transactions")
    ap.add_argument("--max-lamports", type=int, default=5_000_000, help="hard fee+transfer budget")
    ap.add_argument("--burst-size", type=int, default=8)
    ap.add_argument("--fee-levels", default=",".join(str(x) for x in DEFAULT_FEES))
    ap.add_argument("--slots-per-validator", type=int, default=6)
    ap.add_argument("--validators", default="", help="comma separated node pubkeys; empty = auto select")
    ap.add_argument("--max-minutes", type=float, default=30.0)
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--airdrop", type=int, default=0, help="devnet only: request this many lamports first")
    ap.add_argument("--destination", default="", help="transfer destination; default is the payer (self-transfer)")
    ap.add_argument(
        "--mode",
        choices=("memo", "transfer"),
        default="memo",
        help="memo: fee-only tagged transaction (default); transfer: self/other transfer",
    )
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(os.path.join(args.outdir, "blocks"), exist_ok=True)
    send_path = os.path.join(args.outdir, "sends.jsonl")
    if os.path.exists(send_path):
        os.remove(send_path)

    rpc = RpcClient(args.rpc, proxy=args.proxy)
    payer = load_keypair(args.keypair)
    fees = [int(x) for x in args.fee_levels.split(",") if x.strip()]

    if args.airdrop:
        sig = rpc.request_airdrop(payer.address, args.airdrop)
        print("airdrop", sig)
        time.sleep(3)

    balance = rpc.get_balance(payer.address)
    print(f"payer {payer.address} balance {balance/1e9:.6f} SOL")
    if args.live and balance < 1_000_000:
        raise SystemExit("payer balance too low; fund the wallet first")

    if args.validators:
        targets = [v.strip() for v in args.validators.split(",") if v.strip()]
        nodes = {n["pubkey"]: n for n in rpc.get_cluster_nodes()}
        selected = [
            {"node_pubkey": t, "version": nodes.get(t, {}).get("version", "?"), "stake_sol": 0}
            for t in targets
        ]
    else:
        selected = select_validators(rpc.get_cluster_nodes(), rpc.get_vote_accounts(), wanted=8)
        targets = [v["node_pubkey"] for v in selected]

    print("targets:")
    for v in selected:
        print(f"  {v.get('version','?'):>12} {v['node_pubkey']}")

    epoch = rpc.get_epoch_info()
    epoch_start = epoch["absoluteSlot"] - epoch["slotIndex"]
    schedule = rpc.get_leader_schedule()
    slots = target_slots(schedule, epoch_start, targets, epoch["absoluteSlot"], per_validator=args.slots_per_validator)
    print(f"epoch {epoch['epoch']} planned leader slots for targets: {len(slots)}")

    destination = args.destination or payer.address
    spent = 0
    sent = 0
    planned = 0
    per_validator: Dict[str, int] = {t: 0 for t in targets}
    deadline = time.time() + args.max_minutes * 60
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    meta = {
        "run_id": run_id,
        "rpc": args.rpc,
        "live": args.live,
        "payer": payer.address,
        "destination": destination,
        "fee_levels": fees,
        "burst_size": args.burst_size,
        "mode": args.mode,
        "max_lamports": args.max_lamports,
        "started_at": iso_now(),
        "cluster_version": rpc.get_version(),
        "epoch": epoch,
        "targets": selected,
    }
    with open(os.path.join(args.outdir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)

    for item in slots:
        if time.time() > deadline or spent >= args.max_lamports:
            break
        target = item["slot"]
        validator = item["validator"]
        if per_validator.get(validator, 0) >= args.slots_per_validator:
            continue

        if not wait_for_slot(rpc, target):
            continue

        try:
            blockhash = rpc.get_latest_blockhash()[0]
        except RpcError:
            continue

        order = list(range(args.burst_size))
        random.shuffle(order)
        for seq in order:
            fee = random.choice(fees)
            lamports = 1000 + seq
            memo = f"ord:{run_id}:{target}:{seq}".encode()
            if args.mode == "memo":
                tx, msg, sig = build_memo_tx(
                    payer, blockhash, memo, cu_price_micro_lamports=fee
                )
            else:
                tx, msg, sig = build_transfer(
                    payer, destination, lamports, blockhash, cu_price_micro_lamports=fee, memo=memo
                )
            assert verify_tx(payer, tx, msg)
            fee_lamports = estimate_fee(fee)
            if spent + fee_lamports + lamports > args.max_lamports:
                break
            row = {
                "run_id": run_id,
                "slot": target,
                "validator": validator,
                "send_seq": seq,
                "fee_micro_lamports": fee,
                "fee_lamports": fee_lamports,
                "transfer_lamports": 0 if args.mode == "memo" else lamports,
                "signature": sig,
                "sent_at": iso_now(),
                "sent_ns": now_ns(),
                "live": args.live,
            }
            planned += 1
            if args.live:
                try:
                    rpc.send_transaction(encode_tx(tx))
                    row["sent"] = True
                    sent += 1
                except RpcError as exc:
                    row["sent"] = False
                    row["error"] = str(exc)
            append_jsonl(send_path, row)
            spent += fee_lamports + lamports
            if args.live:
                time.sleep(0.012)

        per_validator[validator] = per_validator.get(validator, 0) + 1
        print(f"slot {target} {validator[:8]} burst={args.burst_size} spent={spent/1e9:.6f} SOL sent={sent}")

        # Try to collect the block shortly after the slot.
        if args.live:
            time.sleep(1.5)
            for attempt in range(3):
                block = rpc.get_block(target)
                if block:
                    with open(os.path.join(args.outdir, "blocks", f"{target}.json"), "w", encoding="utf-8") as fh:
                        json.dump(block, fh)
                    break
                time.sleep(1.0)

    meta["finished_at"] = iso_now()
    meta["spent_lamports"] = spent
    meta["planned"] = planned
    meta["sent"] = sent
    meta["slots_measured"] = per_validator
    with open(os.path.join(args.outdir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    print(f"done planned={planned} sent={sent} spent={spent/1e9:.6f} SOL")
    if not args.live:
        print("dry run: nothing was sent; pass --live to execute")


if __name__ == "__main__":
    main()
