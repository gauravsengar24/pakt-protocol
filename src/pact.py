"""
PAKT Pact — Agreement lifecycle state machine.

A Pact is a binding agreement between a Buyer and Seller agent,
enforced by a Kaspa covenant UTXO. The lifecycle is:

  DRAFT → NEGOTIATING → AGREED → FUNDING → LOCKED →
    DELIVERED → VERIFIED → SETTLED
    LOCKED → REFUNDING → REFUNDED
    LOCKED → DISPUTE → ARBITRATING → SETTLED

Each state transition is driven by on-chain events (DAA score,
mempool confirmation, UTXO spending) fed through the wRPC client.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class PactState(Enum):
    DRAFT = "draft"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    FUNDING = "funding"
    LOCKED = "locked"
    DELIVERED = "delivered"
    VERIFIED = "verified"
    SETTLED = "settled"
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    DISPUTE = "dispute"
    ARBITRATING = "arbitrating"
    FAILED = "failed"
    EXPIRED = "expired"

    def terminal(self) -> bool:
        return self in (PactState.SETTLED, PactState.REFUNDED, PactState.FAILED, PactState.EXPIRED)

    def can_transition_to(self, target: PactState) -> bool:
        transitions = {
            PactState.DRAFT: [PactState.NEGOTIATING, PactState.FAILED],
            PactState.NEGOTIATING: [PactState.AGREED, PactState.DRAFT, PactState.FAILED, PactState.NEGOTIATING],
            PactState.AGREED: [PactState.FUNDING, PactState.NEGOTIATING, PactState.FAILED],
            PactState.FUNDING: [PactState.LOCKED, PactState.FAILED, PactState.REFUNDING],
            PactState.LOCKED: [PactState.DELIVERED, PactState.REFUNDING, PactState.DISPUTE, PactState.EXPIRED, PactState.LOCKED],
            PactState.DELIVERED: [PactState.VERIFIED, PactState.DISPUTE],
            PactState.VERIFIED: [PactState.SETTLED, PactState.DISPUTE],
            PactState.SETTLED: [],
            PactState.REFUNDING: [PactState.REFUNDED, PactState.FAILED],
            PactState.REFUNDED: [],
            PactState.DISPUTE: [PactState.ARBITRATING, PactState.REFUNDING, PactState.SETTLED, PactState.DISPUTE],
            PactState.ARBITRATING: [PactState.SETTLED, PactState.FAILED],
            PactState.FAILED: [],
            PactState.EXPIRED: [PactState.REFUNDING, PactState.REFUNDED],
        }
        return target in transitions.get(self, [])


@dataclass
class PactTerms:
    title: str = ""
    description: str = ""
    price_sompi: int = 10_000_000_000  # 100 KAS
    timeout_daa_delta: int = 100
    arb_timeout_daa_delta: int = 50
    content_type: str = "text/markdown"
    content_hash: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "price_sompi": self.price_sompi,
            "price_kas": self.price_sompi / 100_000_000,
            "timeout_daa_delta": self.timeout_daa_delta,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "metadata": self.metadata,
        }


@dataclass
class PactEvent:
    kind: str
    pact_id: str
    state: PactState
    data: dict
    timestamp: float = field(default_factory=time.time)


PactEventHandler = Callable[[PactEvent], None]


@dataclass
class Pact:
    id: str
    state: PactState = PactState.DRAFT
    terms: PactTerms = field(default_factory=PactTerms)
    buyer_address: str = ""
    seller_address: str = ""
    arb_address: str = ""
    buyer_pubkey: str = ""
    seller_pubkey: str = ""
    arb_pubkey: str = ""
    covenant_address: str = ""
    funding_txid: Optional[str] = None
    claim_txid: Optional[str] = None
    refund_txid: Optional[str] = None
    delivery_hash: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[PactEvent] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    _handlers: list[PactEventHandler] = field(default_factory=list, repr=False)

    def on(self, handler: PactEventHandler):
        self._handlers.append(handler)

    def _transition(self, new_state: PactState, data: Optional[dict] = None):
        if not self.state.can_transition_to(new_state):
            raise ValueError(f"Cannot transition from {self.state.value} to {new_state.value}")
        event = PactEvent(
            kind=f"state:{self.state.value}->{new_state.value}",
            pact_id=self.id,
            state=new_state,
            data=data or {},
        )
        self.history.append(event)
        self.state = new_state
        self.updated_at = time.time()
        for handler in self._handlers:
            handler(event)

    def negotiate(self, terms: PactTerms):
        self.terms = terms
        self._transition(PactState.NEGOTIATING, {"terms": terms.to_dict()})

    def agree(self):
        self._transition(PactState.AGREED)

    def fund(self, funding_txid: str, covenant_address: str):
        self.funding_txid = funding_txid
        self.covenant_address = covenant_address
        self._transition(PactState.FUNDING, {"txid": funding_txid, "covenant_address": covenant_address})

    def lock(self, daa_score: int):
        self._transition(PactState.LOCKED, {"daa_score": daa_score})

    def deliver(self, delivery_hash: str):
        self.delivery_hash = delivery_hash
        self._transition(PactState.DELIVERED, {"delivery_hash": delivery_hash})

    def verify(self, valid: bool, reason: str = "delivery_verification_failed"):
        if valid:
            self._transition(PactState.VERIFIED)
        else:
            self._transition(PactState.DISPUTE, {"reason": reason})

    def settle(self, txid: str):
        self.claim_txid = txid
        self._transition(PactState.SETTLED, {"txid": txid})

    def refund(self, txid: str):
        self.refund_txid = txid
        self._transition(PactState.REFUNDED, {"txid": txid})

    def expire(self):
        self._transition(PactState.EXPIRED)

    def dispute(self, reason: str):
        self._transition(PactState.DISPUTE, {"reason": reason})

    def arbitrate(self, txid: str, seller_share: float):
        self.claim_txid = txid
        self._transition(PactState.SETTLED, {"txid": txid, "arbitrated": True, "seller_share": seller_share})

    def fail(self, reason: str):
        self._transition(PactState.FAILED, {"reason": reason})

    def summary(self) -> dict:
        return {
            "id": self.id,
            "state": self.state.value,
            "terms": self.terms.to_dict(),
            "buyer": self.buyer_address,
            "seller": self.seller_address,
            "covenant_address": self.covenant_address,
            "funding_txid": self.funding_txid,
            "claim_txid": self.claim_txid,
            "refund_txid": self.refund_txid,
            "duration_s": round(time.time() - self.created_at, 1),
            "history_count": len(self.history),
        }


class PactManager:
    """Manages multiple active pacts and their lifecycle."""

    def __init__(self):
        self._pacts: dict[str, Pact] = {}
        self._handlers: list[PactEventHandler] = []

    def create(self, pact_id: Optional[str] = None) -> Pact:
        pid = pact_id or f"pakt_{int(time.time())}_{len(self._pacts)}"
        pact = Pact(id=pid)
        for handler in self._handlers:
            pact.on(handler)
        self._pacts[pid] = pact
        return pact

    def get(self, pact_id: str) -> Optional[Pact]:
        return self._pacts.get(pact_id)

    def active(self) -> list[Pact]:
        return [p for p in self._pacts.values() if not p.state.terminal()]

    def completed(self) -> list[Pact]:
        return [p for p in self._pacts.values() if p.state.terminal()]

    def on_event(self, handler: PactEventHandler):
        self._handlers.append(handler)

    def summary(self) -> dict:
        return {
            "total": len(self._pacts),
            "active": len(self.active()),
            "completed": len(self.completed()),
            "pacts": {pid: p.summary() for pid, p in self._pacts.items()},
        }
