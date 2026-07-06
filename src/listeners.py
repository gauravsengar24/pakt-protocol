"""
PAKT Event Listeners — Real-time on-chain event monitoring.

Bridges Kaspa's wRPC event stream to the PAKT pact state machine.
Agents react to DAA score changes, mempool transactions, and UTXO
state transitions without polling.

Architecture:
  ┌─────────────┐    wRPC subscription    ┌──────────────┐
  │  Kaspa Node │ ◄──────────────────────► │ PaktClient   │
  └─────────────┘                          └──────┬───────┘
                                                  │ NetworkEvent
                                                  ▼
                                          ┌──────────────┐
                                          │ PactListener  │
                                          │  (dispatcher) │
                                          └──┬────┬────┬──┘
                                   ┌─────────┘    │    └─────────┐
                                   ▼              ▼              ▼
                             Buyer Agent   Seller Agent   Arbiter Agent
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.client import PaktClient, NetworkEvent
from src.pact import Pact, PactManager, PactState
from src.agent import BuyerAgent, SellerAgent, ArbiterAgent
from src.config import AppConfig

logger = logging.getLogger("pakt.listeners")


class PactListener:
    """
    Listens for on-chain events and drives pact state transitions.

    Mapping:
      NetworkEvent          → Pact Action
      ─────────────────────────────────────────────────
      daa_score             → check timeouts, trigger refunds
      tx_accepted           → detect covenant funding/locking
      utxo_changed          → detect covenant spending (claim/refund)
      block_added           → verify confirmations
    """

    def __init__(self, client: PaktClient, pact_mgr: PactManager,
                 buyer: BuyerAgent, seller: SellerAgent, arbiter: ArbiterAgent,
                 config: AppConfig):
        self.client = client
        self.pact_mgr = pact_mgr
        self.buyer = buyer
        self.seller = seller
        self.arbiter = arbiter
        self.config = config
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Register all event handlers and start monitoring."""
        self.client.on("daa_score", self._on_daa_score)
        self.client.on("tx_accepted", self._on_tx_accepted)
        self.client.on("utxo_changed", self._on_utxo_changed)

        daa_task = await self.client.start_daa_monitor(
            interval=self.config.network.daa_score_poll_interval,
        )
        mempool_task = await self.client.start_mempool_monitor()

        self._tasks = [daa_task, mempool_task]
        logger.info("PactListener started — monitoring DAA + mempool")

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def watch_covenant(self, pact: Pact):
        """Start monitoring UTXO changes for a specific covenant."""
        if pact.covenant_address:
            task = await self.client.monitor_covenant_utxo(pact.covenant_address)
            self._tasks.append(task)

    # ── Event Handlers ──────────────────────────────────────────────────

    async def _on_daa_score(self, event: NetworkEvent):
        daa = event.data["daa_score"]
        for pact in self.pact_mgr.active():
            if pact.state == PactState.LOCKED:
                timeout = pact.terms.timeout_daa_delta + self._funding_daa(pact)
                if daa >= timeout:
                    logger.info(f"Pact {pact.id[:8]} timed out at DAA {daa}")
                    pact.expire()
                    await self._handle_timeout(pact)

    async def _on_tx_accepted(self, event: NetworkEvent):
        tx_id = event.data["tx_id"]
        daa_score = event.data.get("daa_score", 0)
        for pact in self.pact_mgr.active():
            if pact.state == PactState.FUNDING and pact.funding_txid == tx_id:
                logger.info(f"Pact {pact.id[:8]} funding confirmed at DAA {daa_score}")
                pact.lock(daa_score)

    async def _on_utxo_changed(self, event: NetworkEvent):
        address = event.data.get("address", "")
        utxo_count = event.data.get("utxo_count", 0)
        for pact in self.pact_mgr.active():
            if pact.covenant_address == address:
                if utxo_count == 0:
                    logger.info(f"Pact {pact.id[:8]} covenant UTXO spent — settled or refunded")
                else:
                    logger.info(f"Pact {pact.id[:8]} covenant UTXO count: {utxo_count}")

    # ── Timeout Handling ────────────────────────────────────────────────

    async def _handle_timeout(self, pact: Pact):
        logger.info(f"Pact {pact.id[:8]} — triggering refund path")
        authorized = await self.buyer.authorize_refund(pact)
        if authorized:
            pact.refund(f"refund_{pact.id[:8]}")

    def _funding_daa(self, pact: Pact) -> int:
        for event in pact.history:
            if event.state == PactState.LOCKED:
                return event.data.get("daa_score", 0)
        return 0


# ── Convenience: Automated Pact Reaction ──────────────────────────────────────

async def auto_execute_pact(pact: Pact, client: PaktClient,
                             buyer: BuyerAgent, seller: SellerAgent, arbiter: ArbiterAgent,
                             pact_mgr: PactManager, config: AppConfig) -> Pact:
    """
    Automatically execute a pact by monitoring on-chain events.

    This is the production version of PactExecutor.run_full_lifecycle()
    that uses real wRPC events instead of simulated state transitions.
    """
    listener = PactListener(client, pact_mgr, buyer, seller, arbiter, config)

    await listener.start()
    await listener.watch_covenant(pact)

    try:
        funding_daa = await client.get_daa_score()
        pact.terms.timeout_daa_delta = funding_daa + config.covenant.timeout_daa_delta

        for _ in range(300):
            await asyncio.sleep(1)

            if pact.state.terminal():
                logger.info(f"Pact {pact.id[:8]} reached terminal state: {pact.state.value}")
                break

        return pact

    finally:
        await listener.stop()
