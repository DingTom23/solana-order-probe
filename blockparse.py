"""Parse getBlock output across legacy and v1 transactions.

The public RPC's jsonParsed encoding does not expand every instruction in a
v1 transaction: those instructions carry `programId` and base58 `data` but no
`program`/`parsed` fields. This module normalises both shapes.
"""
from __future__ import annotations

from typing import List, Optional

from solana_tx import COMPUTE_BUDGET_PROGRAM, b58decode

VOTE_PROGRAM = "Vote111111111111111111111111111111111111111"


def _decode_data(ix: dict) -> Optional[bytes]:
    data = ix.get("data")
    if not isinstance(data, str):
        return None
    try:
        return b58decode(data)
    except ValueError:
        return None


def priority_micro_lamports(ix: dict) -> Optional[int]:
    """Return SetComputeUnitPrice for legacy (parsed) and v1 (raw) shapes."""
    program = ix.get("program")
    parsed = ix.get("parsed")
    if program == "compute-budget" and isinstance(parsed, dict):
        if parsed.get("type") == "setComputeUnitPrice":
            return int(parsed.get("info", {}).get("microLamports", 0))
    if program is None and ix.get("programId") == COMPUTE_BUDGET_PROGRAM:
        data = _decode_data(ix)
        if data and len(data) >= 9 and data[0] == 3:
            return int.from_bytes(data[1:9], "little")
    return None


def is_vote(ix: dict) -> bool:
    if ix.get("program") == "vote":
        return True
    pid = ix.get("programId") or ""
    return pid == VOTE_PROGRAM


def parse_transactions(block: dict) -> List[dict]:
    rows = []
    for index, item in enumerate(block.get("transactions", [])):
        tx = item.get("transaction", {})
        meta = item.get("meta") or {}
        message = tx.get("message", {})
        keys = message.get("accountKeys", [])
        first = keys[0] if keys else {}
        payer = first.get("pubkey") if isinstance(first, dict) else first
        instructions = message.get("instructions", []) or []
        priority = None
        transfers = []
        vote = False
        for ix in instructions:
            if is_vote(ix):
                vote = True
            value = priority_micro_lamports(ix)
            if value is not None:
                priority = value
            parsed = ix.get("parsed")
            if ix.get("program") == "system" and isinstance(parsed, dict) and parsed.get("type") == "transfer":
                info = parsed.get("info", {})
                transfers.append(
                    {
                        "source": info.get("source"),
                        "destination": info.get("destination"),
                        "lamports": info.get("lamports"),
                    }
                )
        rows.append(
            {
                "index": index,
                "signature": (tx.get("signatures") or [None])[0],
                "payer": payer,
                "fee": meta.get("fee"),
                "cu": meta.get("computeUnitsConsumed"),
                "priority_micro_lamports": priority,
                "vote": vote,
                "transfers": transfers,
                "err": meta.get("err"),
                "version": item.get("version", "legacy"),
            }
        )
    return rows
