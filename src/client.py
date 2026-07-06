"""
PAKT wRPC Client — Kaspa network connection + real-time event listeners.

Provides:
  - Async connection management with auto-reconnect
  - Balance and UTXO queries
  - Transaction submission and confirmation tracking
  - Real-time mempool subscription (DAA score, new transactions)
  - Covenant UTXO monitoring via wRPC notifications

Built on the Kaspa Python SDK's RpcClient with subscription support
for sub-second agent state transitions.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncGenerator, Callable, Optional

from src.config import NetworkConfig

logger = logging.getLogger("pakt.client")


@dataclass
class NetworkEvent:
    kind: str  # "daa_score" | "tx_accepted" | "utxo_changed" | "block_added"
    data: dict
    timestamp: float = field(default_factory=lambda: __import__("time").time())


EventHandler = Callable[[NetworkEvent], None]


class PaktClient:
    """
    wRPC client wrapper for the PAKT protocol.

    Wraps kaspa.RpcClient with connection lifecycle, event subscriptions,
    and automatic reconnection.
    """

    def __init__(self, config: NetworkConfig):
        self.config = config
        self._rpc = None
        self._connected = False
        self._handlers: dict[str, list[EventHandler]] = {}
        self._sub_tasks: list[asyncio.Task] = []
        self._current_daa: int = 0

    async def connect(self):
        from kaspa import RpcClient
        self._rpc = RpcClient(self.config.wrpc_endpoint)
        try:
            await self._rpc.connect()
            self._connected = True
            info = await self._rpc.get_info()
            self._current_daa = info.daa_score
            logger.info(f"Connected to {self.config.id} (DAA: {self._current_daa})")
        except Exception as e:
            self._connected = False
            logger.error(f"Connection failed: {e}")
            raise

    async def disconnect(self):
        for task in self._sub_tasks:
            task.cancel()
        if self._rpc and self._connected:
            await self._rpc.disconnect()
        self._connected = False

    async def reconnect(self):
        await self.disconnect()
        await asyncio.sleep(1)
        await self.connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def daa_score(self) -> int:
        return self._current_daa

    async def get_balance(self, address: str) -> int:
        result = await self._rpc.get_balance_by_address(address)
        return result.balance if hasattr(result, "balance") else result

    async def get_utxos(self, address: str) -> list[dict]:
        result = await self._rpc.get_utxos_by_address(address)
        return [dict(utxo) for utxo in result]

    async def submit_transaction(self, tx_hex: str) -> str:
        tx_id = await self._rpc.submit_transaction(tx_hex)
        logger.info(f"Submitted tx: {tx_id}")
        return tx_id

    async def wait_for_tx(self, tx_id: str, timeout: float = 60.0) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            try:
                tx = await self._rpc.get_transaction(tx_id)
                if tx and getattr(tx, "confirmations", 0) > 0:
                    return {"tx_id": tx_id, "confirmations": tx.confirmations}
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise TimeoutError(f"Transaction {tx_id} not confirmed within {timeout}s")

    async def get_daa_score(self) -> int:
        info = await self._rpc.get_info()
        self._current_daa = info.daa_score
        return self._current_daa

    # ── Event Subscriptions ──────────────────────────────────────────────

    def on(self, event_kind: str, handler: EventHandler):
        self._handlers.setdefault(event_kind, []).append(handler)

    def off(self, event_kind: str, handler: EventHandler):
        self._handlers.setdefault(event_kind, []).remove(handler)

    async def _emit(self, event: NetworkEvent):
        for handler in self._handlers.get(event.kind, []):
            try:
                handler(event)
            except Exception as e:
                logger.warning(f"Handler error for {event.kind}: {e}")

    async def start_daa_monitor(self, interval: float = 0.5):
        """Poll DAA score at sub-second intervals for fast state detection."""

        async def _poll():
            while True:
                try:
                    score = await self.get_daa_score()
                    if score != self._current_daa:
                        old = self._current_daa
                        self._current_daa = score
                        await self._emit(NetworkEvent(
                            kind="daa_score",
                            data={"daa_score": score, "previous": old, "delta": score - old},
                        ))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"DAA poll error: {e}")
                await asyncio.sleep(interval)

        task = asyncio.create_task(_poll())
        self._sub_tasks.append(task)
        return task

    async def start_mempool_monitor(self):
        """Subscribe to mempool transaction notifications."""

        async def _subscribe():
            try:
                async for notif in self._rpc.subscribe_transactions():
                    await self._emit(NetworkEvent(
                        kind="tx_accepted",
                        data={
                            "tx_id": notif.transaction_id,
                            "daa_score": notif.daa_score,
                            "is_accepted": notif.is_accepted,
                        },
                    ))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Mempool subscription error: {e}")

        task = asyncio.create_task(_subscribe())
        self._sub_tasks.append(task)
        return task

    async def monitor_covenant_utxo(self, covenant_address: str):
        """
        Subscribe to UTXO changes for a specific covenant address.
        Poll-based since wRPC may not support per-address subscriptions.
        """
        last_tx_count = 0

        async def _poll():
            nonlocal last_tx_count
            while True:
                try:
                    utxos = await self.get_utxos(covenant_address)
                    if len(utxos) != last_tx_count:
                        last_tx_count = len(utxos)
                        await self._emit(NetworkEvent(
                            kind="utxo_changed",
                            data={
                                "address": covenant_address,
                                "utxo_count": len(utxos),
                                "utxos": utxos,
                            },
                        ))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Covenant UTXO poll error: {e}")
                await asyncio.sleep(1.0)

        task = asyncio.create_task(_poll())
        self._sub_tasks.append(task)
        return task


@asynccontextmanager
async def pakt_connection(config: NetworkConfig) -> AsyncGenerator[PaktClient, None]:
    client = PaktClient(config)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
