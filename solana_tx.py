"""Minimal Solana transaction builder (stdlib + cryptography).

No solana-py dependency: builds, signs and serializes legacy transactions
so the measurement harness stays reproducible inside a small container.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


# ---------------------------------------------------------------- base58
def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out: List[str] = []
    while n:
        n, r = divmod(n, 58)
        out.append(ALPHABET[r])
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + "".join(reversed(out))


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + ALPHABET.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


def compact_u16(value: int) -> bytes:
    out = bytearray()
    while True:
        elem = value & 0x7F
        value >>= 7
        if value == 0:
            out.append(elem)
            return bytes(out)
        out.append(elem | 0x80)


# ---------------------------------------------------------------- keys
@dataclass
class Keypair:
    seed: bytes
    public_key: bytes

    @property
    def address(self) -> str:
        return b58encode(self.public_key)

    def sign(self, message: bytes) -> bytes:
        sk = Ed25519PrivateKey.from_private_bytes(self.seed)
        return sk.sign(message)

    def secret_key_base58(self) -> str:
        return b58encode(self.seed + self.public_key)


def keypair_from_seed(seed: bytes) -> Keypair:
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    pub = sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return Keypair(seed=seed, public_key=pub)


def load_keypair(path: str) -> Keypair:
    """Accepts our wallet JSON, a Phantom base58 secret, or a 64-byte array."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().strip()
    if raw.startswith("["):
        data = bytes(json.loads(raw))
        if len(data) == 64:
            return keypair_from_seed(data[:32])
        if len(data) == 32:
            return keypair_from_seed(data)
        raise ValueError("unsupported key array length")
    if raw.startswith("{"):
        obj = json.loads(raw)
        if "seed_hex" in obj:
            return keypair_from_seed(bytes.fromhex(obj["seed_hex"]))
        if "secret_key_base58_phantom" in obj:
            data = b58decode(obj["secret_key_base58_phantom"])
            return keypair_from_seed(data[:32])
    data = b58decode(raw)
    if len(data) == 64:
        return keypair_from_seed(data[:32])
    raise ValueError("unsupported key file format")


def random_keypair() -> Keypair:
    return keypair_from_seed(os.urandom(32))


# ---------------------------------------------------------------- message
@dataclass
class Instruction:
    program_id: str
    accounts: Sequence[int]
    data: bytes


def _account_metas(keys: Sequence[str]):
    return [b58decode(k) for k in keys]


def build_message(
    payer: str,
    keys: Sequence[str],
    blockhash: str,
    instructions: Sequence[Instruction],
    num_required_signatures: int = 1,
    num_readonly_signed: int = 0,
    num_readonly_unsigned: int = 2,
) -> bytes:
    header = bytes([num_required_signatures, num_readonly_signed, num_readonly_unsigned])
    out = bytearray(header)
    out += compact_u16(len(keys))
    for key in keys:
        out += b58decode(key) if isinstance(key, str) else key
    out += b58decode(blockhash)
    out += compact_u16(len(instructions))
    for ix in instructions:
        out.append(keys.index(ix.program_id))
        out += compact_u16(len(ix.accounts))
        for idx in ix.accounts:
            out.append(idx)
        out += compact_u16(len(ix.data))
        out += ix.data
    return bytes(out)


def transfer_instruction(from_idx: int, to_idx: int, lamports: int) -> Instruction:
    data = bytes([2, 0, 0, 0]) + lamports.to_bytes(8, "little")
    return Instruction(SYSTEM_PROGRAM, [from_idx, to_idx], data)


def compute_unit_price_instruction(micro_lamports: int) -> Instruction:
    data = bytes([3]) + micro_lamports.to_bytes(8, "little")
    return Instruction(COMPUTE_BUDGET_PROGRAM, [], data)


def compute_unit_limit_instruction(units: int) -> Instruction:
    data = bytes([2]) + units.to_bytes(4, "little")
    return Instruction(COMPUTE_BUDGET_PROGRAM, [], data)


def memo_instruction(memo: bytes) -> Instruction:
    return Instruction(MEMO_PROGRAM, [], memo)


def sign_transaction(kp: Keypair, message: bytes) -> bytes:
    signature = kp.sign(message)
    return compact_u16(1) + signature + message


def build_transfer(
    payer: Keypair,
    destination: str,
    lamports: int,
    blockhash: str,
    cu_price_micro_lamports: int = 0,
    cu_limit: int = 200,
    memo: Optional[bytes] = None,
) -> Tuple[bytes, bytes, str]:
    """Returns (serialized_tx, message_bytes, signature_b58).

    Destination may equal the payer (self-transfer); account keys are
    deduplicated because a message must not repeat a pubkey.
    """
    keys = [payer.address]
    if destination != payer.address:
        keys.append(destination)
    keys.append(SYSTEM_PROGRAM)
    keys.append(COMPUTE_BUDGET_PROGRAM)
    if memo is not None:
        keys.append(MEMO_PROGRAM)
    readonly_unsigned = len(keys) - (2 if destination != payer.address else 1)
    from_idx = keys.index(payer.address)
    to_idx = keys.index(destination)
    ixs = [
        compute_unit_limit_instruction(cu_limit),
        compute_unit_price_instruction(cu_price_micro_lamports),
        transfer_instruction(from_idx, to_idx, lamports),
    ]
    if memo is not None:
        ixs.append(memo_instruction(memo))
    message = build_message(
        payer.address,
        keys,
        blockhash,
        ixs,
        num_readonly_unsigned=readonly_unsigned,
    )
    tx = sign_transaction(payer, message)
    signature = b58encode(tx[1:65])
    return tx, message, signature


def build_memo_tx(
    payer: Keypair,
    blockhash: str,
    memo: bytes,
    cu_price_micro_lamports: int = 0,
    cu_limit: int = 200,
) -> Tuple[bytes, bytes, str]:
    """A fee-paying transaction whose only effect is a memo.

    No account is created and no lamports move, so a long measurement run
    cannot accidentally drain a destination below rent exemption.
    """
    keys = [payer.address, COMPUTE_BUDGET_PROGRAM, MEMO_PROGRAM]
    ixs = [
        compute_unit_limit_instruction(cu_limit),
        compute_unit_price_instruction(cu_price_micro_lamports),
        memo_instruction(memo),
    ]
    message = build_message(
        payer.address,
        keys,
        blockhash,
        ixs,
        num_readonly_unsigned=2,
    )
    tx = sign_transaction(payer, message)
    signature = b58encode(tx[1:65])
    return tx, message, signature


def encode_tx(tx: bytes) -> str:
    return base64.b64encode(tx).decode("ascii")


def verify_tx(payer: Keypair, tx: bytes, message: bytes) -> bool:
    sig = tx[1:65]
    pub = Ed25519PublicKey.from_public_bytes(payer.public_key)
    try:
        pub.verify(sig, message)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- tests
def _selftest() -> None:
    assert b58encode(b"hello world") == "StV1DL6CwTryKyV"
    assert b58decode("StV1DL6CwTryKyV") == b"hello world"
    assert b58encode(b"\x00\x00\x01") == "112"
    assert compact_u16(0) == b"\x00"
    assert compact_u16(127) == b"\x7f"
    assert compact_u16(128) == b"\x80\x01"
    kp = random_keypair()
    dest = random_keypair().address
    blockhash = b58encode(os.urandom(32))
    tx, msg, sig = build_transfer(kp, dest, 1234, blockhash, 1000)
    assert verify_tx(kp, tx, msg)
    assert b58encode(tx[1:65]) == sig
    print("selftest ok", kp.address, "tx_bytes", len(tx), "sig", sig[:8])


if __name__ == "__main__":
    _selftest()
