"""Tiny JSON-RPC client for Solana with optional HTTP proxy support."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, List, Optional, Sequence, Tuple


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"rpc error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class RpcClient:
    def __init__(self, url: str, proxy: Optional[str] = None, timeout: int = 30):
        self.url = url
        self.timeout = timeout
        handlers = []
        proxy = proxy or os.environ.get("RPC_PROXY")
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self.opener = urllib.request.build_opener(*handlers)
        self._id = 0

    def _post(self, payload: Any, retries: int = 4) -> Any:
        body = json.dumps(payload).encode("utf-8")
        last: Optional[Exception] = None
        for attempt in range(retries):
            req = urllib.request.Request(
                self.url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "solana-order-harness"},
            )
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"rpc transport failed after {retries} attempts: {last}")

    def call(self, method: str, params: Optional[Sequence[Any]] = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": list(params or [])}
        out = self._post(payload)
        if "error" in out:
            err = out["error"]
            raise RpcError(err.get("code", -1), err.get("message", "unknown"), err.get("data"))
        return out.get("result")

    def batch(self, calls: Sequence[Tuple[str, Sequence[Any]]]) -> List[Any]:
        payload = []
        for method, params in calls:
            self._id += 1
            payload.append({"jsonrpc": "2.0", "id": self._id, "method": method, "params": list(params)})
        out = self._post(payload)
        if not isinstance(out, list):
            raise RuntimeError(f"unexpected batch response: {out}")
        results = []
        for item in out:
            if "error" in item:
                err = item["error"]
                results.append(RpcError(err.get("code", -1), err.get("message", "unknown"), err.get("data")))
            else:
                results.append(item.get("result"))
        return results

    # ------------------------------------------------------------ helpers
    def get_version(self) -> dict:
        return self.call("getVersion")

    def get_health(self) -> str:
        return self.call("getHealth")

    def get_slot(self, commitment: str = "confirmed") -> int:
        return self.call("getSlot", [{"commitment": commitment}])

    def get_block_height(self, commitment: str = "confirmed") -> int:
        return self.call("getBlockHeight", [{"commitment": commitment}])

    def get_latest_blockhash(self, commitment: str = "confirmed") -> Tuple[str, int]:
        res = self.call("getLatestBlockhash", [{"commitment": commitment}])
        return res["value"]["blockhash"], res["value"]["lastValidBlockHeight"]

    def get_balance(self, address: str) -> int:
        res = self.call("getBalance", [address, {"commitment": "confirmed"}])
        return res["value"]

    def get_cluster_nodes(self) -> list:
        return self.call("getClusterNodes")

    def get_vote_accounts(self) -> dict:
        return self.call("getVoteAccounts", [{"commitment": "confirmed"}])

    def get_slot_leaders(self, start_slot: int, limit: int) -> List[str]:
        return self.call("getSlotLeaders", [start_slot, limit])

    def get_leader_schedule(self, slot: Optional[int] = None) -> dict:
        params = [slot] if slot is not None else []
        return self.call("getLeaderSchedule", params)

    def get_epoch_info(self) -> dict:
        return self.call("getEpochInfo", [{"commitment": "confirmed"}])

    def get_block(
        self,
        slot: int,
        encoding: str = "jsonParsed",
        transaction_details: str = "full",
        max_version: int = 1,
    ) -> Optional[dict]:
        return self.call(
            "getBlock",
            [
                slot,
                {
                    "encoding": encoding,
                    "transactionDetails": transaction_details,
                    "maxSupportedTransactionVersion": max_version,
                    "rewards": False,
                    "commitment": "confirmed",
                },
            ],
        )

    def send_transaction(self, tx_base64: str, skip_preflight: bool = True) -> str:
        return self.call(
            "sendTransaction",
            [
                tx_base64,
                {
                    "encoding": "base64",
                    "skipPreflight": skip_preflight,
                    "preflightCommitment": "confirmed",
                    "maxRetries": 0,
                },
            ],
        )

    def get_signature_statuses(self, signatures: Sequence[str]) -> list:
        res = self.call("getSignatureStatuses", [list(signatures), {"searchTransactionHistory": True}])
        return res["value"]

    def get_transaction(self, signature: str) -> Optional[dict]:
        return self.call(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": "confirmed",
                },
            ],
        )

    def request_airdrop(self, address: str, lamports: int) -> str:
        return self.call("requestAirdrop", [address, lamports, {"commitment": "confirmed"}])
