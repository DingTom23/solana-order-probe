"""Funding and transaction sanity check. Sends nothing."""
from __future__ import annotations

import argparse
import base64
import os

from rpc import RpcClient
from solana_tx import build_transfer, encode_tx, load_keypair, verify_tx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default=os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com"))
    ap.add_argument("--proxy", default=os.environ.get("RPC_PROXY"))
    ap.add_argument("--keypair", default=os.environ.get("PAYER_KEYPAIR", "wallet/solana.json"))
    ap.add_argument("--lamports", type=int, default=1000)
    args = ap.parse_args()

    rpc = RpcClient(args.rpc, proxy=args.proxy)
    kp = load_keypair(args.keypair)
    balance = rpc.get_balance(kp.address)
    print("cluster:", rpc.get_version().get("solana-core"))
    print("payer:", kp.address)
    print("balance:", balance, "lamports", f"({balance/1e9:.9f} SOL)")

    blockhash = rpc.get_latest_blockhash()[0]
    tx, msg, sig = build_transfer(
        kp, kp.address, args.lamports, blockhash, cu_price_micro_lamports=1_000_000, memo=b"ord:preflight"
    )
    print("signature:", sig)
    print("signature_valid:", verify_tx(kp, tx, msg))
    fee = rpc.call("getFeeForMessage", [base64.b64encode(msg).decode(), {"commitment": "confirmed"}])
    print("rpc_fee_for_message:", fee.get("value"))
    sim = rpc.call(
        "simulateTransaction",
        [
            encode_tx(tx),
            {
                "encoding": "base64",
                "sigVerify": True,
                "replaceRecentBlockhash": False,
                "commitment": "confirmed",
            },
        ],
    )
    value = sim.get("value", {})
    print("simulate_err:", value.get("err"))
    print("simulate_logs:", (value.get("logs") or [])[:8])
    if balance < 10_000_000:
        print("FUNDING NEEDED: send at least 0.01 SOL to the payer before a live run")


if __name__ == "__main__":
    main()
