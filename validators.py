"""Select validators that span multiple client versions/forks."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List

LAMPORTS_PER_SOL = 1_000_000_000


def client_family(version: str) -> str:
    """Best-effort family label from the advertised version string.

    The bounty accepts version strings as the identifier, so this is a
    reporting convenience, not a claim about the binary.
    """
    if not version:
        return "unknown"
    major = version.split(".")[0]
    if major in ("26", "0"):
        return f"non-agave-{major}.x"
    if major in ("1", "2", "3", "4"):
        return f"agave-{major}.x"
    return f"other-{major}.x"


def build_node_index(nodes: Iterable[dict]) -> Dict[str, dict]:
    index = {}
    for node in nodes:
        pubkey = node.get("pubkey")
        if pubkey:
            index[pubkey] = node
    return index


def select_validators(
    nodes: Iterable[dict],
    vote_accounts: dict,
    wanted: int = 8,
    per_family: int = 2,
    min_families: int = 3,
) -> List[dict]:
    node_index = build_node_index(nodes)
    current = vote_accounts.get("current", [])
    ranked = []
    for vote in current:
        node = node_index.get(vote.get("nodePubkey"))
        if not node:
            continue
        version = node.get("version") or ""
        if not version:
            continue
        stake = int(vote.get("activatedStake", 0))
        ranked.append(
            {
                "vote_pubkey": vote.get("votePubkey"),
                "node_pubkey": vote.get("nodePubkey"),
                "stake_sol": round(stake / LAMPORTS_PER_SOL, 2),
                "commission": vote.get("commission"),
                "version": version,
                "family": client_family(version),
                "tpu": node.get("tpu"),
                "tpu_quic": node.get("tpuQuic"),
                "rpc": node.get("rpc"),
                "feature_set": node.get("featureSet"),
                "gossip": node.get("gossip"),
            }
        )
    ranked.sort(key=lambda item: item["stake_sol"], reverse=True)

    by_family: Dict[str, List[dict]] = defaultdict(list)
    for item in ranked:
        by_family[item["family"]].append(item)

    families = sorted(by_family, key=lambda fam: sum(v["stake_sol"] for v in by_family[fam]), reverse=True)
    selected: List[dict] = []
    seen = set()

    # Round-robin across families so at least `min_families` are represented.
    for _ in range(per_family):
        for fam in families:
            for item in by_family[fam]:
                if item["node_pubkey"] in seen:
                    continue
                if sum(1 for s in selected if s["family"] == fam) >= per_family:
                    break
                selected.append(item)
                seen.add(item["node_pubkey"])
                break

    # Top up with the highest-stake remaining validators.
    for item in ranked:
        if len(selected) >= wanted:
            break
        if item["node_pubkey"] not in seen:
            selected.append(item)
            seen.add(item["node_pubkey"])

    selected.sort(key=lambda item: item["stake_sol"], reverse=True)
    if len({s["family"] for s in selected}) < min_families:
        raise RuntimeError("could not cover the required number of client families")
    if len(selected) < 6:
        raise RuntimeError("could not select six validators")
    return selected[:wanted]


def leader_windows(leaders: List[str], targets: Iterable[str], start_slot: int) -> Dict[str, List[int]]:
    wanted = set(targets)
    out: Dict[str, List[int]] = {t: [] for t in wanted}
    for offset, leader in enumerate(leaders):
        if leader in wanted:
            out[leader].append(start_slot + offset)
    return out
