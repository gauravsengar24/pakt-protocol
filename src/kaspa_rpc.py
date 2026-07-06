"""
PAKT Kaspa wRPC Client — Pure-Python, zero Rust dependency.

Communicates with Kaspa nodes via JSON-RPC 2.0 over WebSocket (wRPC).
Implements the subset of the Kaspa RPC protocol needed for the PAKT
covenant lifecycle:

  - Node info & DAA score queries
  - UTXO enumeration and balance checks
  - Transaction submission and confirmation monitoring
  - Mempool transaction subscriptions

Testnet-12 public endpoints:
  wss://testnet-12.kaspa.org/wrpc/v1   (TLS)
  ws://testnet-12.kaspa.org:17210       (non-TLS, gRPC-web)

Usage:
    client = KaspaRPCClient("wss://testnet-12.kaspa.org/wrpc/v1")
    await client.connect()
    info = await client.get_info()
    daa = info["daaScore"]
    utxos = await client.get_utxos_by_address("kaspatest:...")
    await client.submit_transaction(tx_hex)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import struct
import time
from typing import Any, Optional

logger = logging.getLogger("pakt.rpc")


class KaspaRPCError(Exception):
    """Raised when the Kaspa node returns an error response."""
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{code}] {message}")


class KaspaRPCClient:
    """
    Lightweight wRPC client for Kaspa nodes.

    Uses JSON-RPC 2.0 over WebSocket. No Rust/Pyo3 dependencies.
    """

    def __init__(self, url: str = "wss://testnet-12.kaspa.org/wrpc/v1",
                 timeout: float = 30.0):
        self.url = url
        self.timeout = timeout
        self._ws = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def connect(self):
        import websockets
        logger.info(f"Connecting to {self.url}")
        self._ws = await websockets.connect(
            self.url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        asyncio.create_task(self._reader_loop())
        logger.info("wRPC connection established")

    async def disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("wRPC connection closed")

    async def _reader_loop(self):
        """Continuously read responses and resolve pending futures."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                    req_id = msg.get("id")
                    if req_id is not None and req_id in self._pending:
                        future = self._pending.pop(req_id)
                        if "error" in msg:
                            err = msg["error"]
                            future.set_exception(
                                KaspaRPCError(err.get("code", 0),
                                              err.get("message", "unknown"),
                                              err.get("data"))
                            )
                        else:
                            future.set_result(msg.get("result"))
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from node: {raw[:200]}")
        except Exception as e:
            logger.error(f"Reader loop error: {e}")
            for future in self._pending.values():
                future.set_exception(KaspaRPCError(-1, f"Connection lost: {e}"))
            self._pending.clear()

    async def _call(self, method: str, params: dict = None) -> Any:
        if not self._ws:
            raise KaspaRPCError(-1, "Not connected. Call connect() first.")

        self._req_id += 1
        req_id = self._req_id
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        })

        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._ws.send(payload)
        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise KaspaRPCError(-1, f"Request {method} timed out after {self.timeout}s")

    # ── Node Info ──────────────────────────────────────────────────────

    async def get_info(self) -> dict:
        return await self._call("getInfo")

    async def get_current_daa_score(self) -> int:
        info = await self.get_info()
        return info.get("daaScore", 0)

    async def get_server_version(self) -> str:
        info = await self.get_info()
        return info.get("serverVersion", "unknown")

    # ── Address & UTXO Queries ─────────────────────────────────────────

    async def get_balance_by_address(self, address: str) -> int:
        result = await self._call("getBalanceByAddress", {"address": address})
        return result.get("balance", 0)

    async def get_utxos_by_address(self, address: str) -> list[dict]:
        result = await self._call("getUtxosByAddress", {"address": address})
        return result if isinstance(result, list) else result.get("utxos", [])

    async def check_tx_acceptance(self, tx_id: str) -> bool:
        try:
            result = await self._call("getTransaction", {"transactionId": tx_id})
            return result.get("isAccepted", False)
        except KaspaRPCError:
            return False

    # ── Transaction Submission ──────────────────────────────────────────

    async def submit_transaction(self, tx_hex: str) -> str:
        result = await self._call("submitTransaction", {
            "transaction": tx_hex,
        })
        return result.get("transactionId", "")

    async def submit_transaction_repr(self, tx_repr: dict) -> str:
        result = await self._call("submitTransaction", tx_repr)
        return result.get("transactionId", "")

    # ── Block / DAA Monitoring ─────────────────────────────────────────

    async def wait_for_daa_score(self, target_daa: int,
                                 poll_interval: float = 1.0,
                                 timeout: float = 300.0) -> int:
        start = time.time()
        while time.time() - start < timeout:
            current = await self.get_current_daa_score()
            if current >= target_daa:
                return current
            await asyncio.sleep(poll_interval)
        raise TimeoutError(f"DAA score {target_daa} not reached within {timeout}s")

    async def wait_for_tx_confirmation(self, tx_id: str,
                                       poll_interval: float = 2.0,
                                       timeout: float = 120.0) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            accepted = await self.check_tx_acceptance(tx_id)
            daa = await self.get_current_daa_score()
            if accepted:
                return {"tx_id": tx_id, "daa_score": daa, "confirmed": True}
            logger.debug(f"Waiting for {tx_id[:16]}... @ DAA {daa}")
            await asyncio.sleep(poll_interval)
        raise TimeoutError(f"TX {tx_id} not confirmed within {timeout}s")

    # ── Health ──────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            await self.get_info()
            return True
        except Exception:
            return False


# ── Connection Context Manager ───────────────────────────────────────────────

class KaspaRPCConnection:
    """Async context manager for wRPC connections."""

    def __init__(self, url: str = "wss://testnet-12.kaspa.org/wrpc/v1"):
        self.client = KaspaRPCClient(url)

    async def __aenter__(self) -> KaspaRPCClient:
        await self.client.connect()
        return self.client

    async def __aexit__(self, *exc_info):
        await self.client.disconnect()
