"""
PAKT AI Agents — Autonomous negotiation and pact execution.

Buyer and Seller agents negotiate pact terms, execute the covenant
lifecycle, and handle dispute resolution. Agents use LLMs for
natural-language negotiation but all commitments, payments, and
settlements are on-chain via Kaspa covenants.

Architecture:
  - BaseAgent: shared wallet, LLM integration, event handling
  - BuyerAgent: initiates pacts, evaluates proposals, verifies delivery
  - SellerAgent: responds to proposals, generates content, claims payment
  - ArbiterAgent: resolves disputes with on-chain split
  - NegotiationEngine: orchestrates the full negotiation protocol
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.pact import Pact, PactManager, PactState, PactTerms
from src.wallet import Wallet, WalletRegistry
from src.client import PaktClient, NetworkEvent
from src.config import AppConfig

logger = logging.getLogger("pakt.agent")


# ── Agent Communication ─────────────────────────────────────────────────────

@dataclass
class AgentMessage:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "message_id": self.message_id,
        }


NegotiationCallback = Callable[[list[AgentMessage]], None]


# ── Abstract Base Agent ─────────────────────────────────────────────────────

class BaseAgent(ABC):
    """Shared agent foundation with wallet, LLM, and event handling."""

    def __init__(self, role: str, wallet: Wallet, config: AppConfig):
        self.role = role
        self.wallet = wallet
        self.config = config
        self._message_log: list[AgentMessage] = []
        self._listeners: list[NegotiationCallback] = []

    @property
    def address(self) -> str:
        return self.wallet.address or "unknown"

    @property
    def pubkey(self) -> bytes:
        return self.wallet.pubkey or b""

    @property
    def pubkey_hex(self) -> str:
        return self.wallet.pubkey_hex or ""

    def log_message(self, role: str, content: str) -> AgentMessage:
        msg = AgentMessage(role=role, content=content)
        self._message_log.append(msg)
        for listener in self._listeners:
            listener([msg])
        return msg

    def on_message(self, callback: NegotiationCallback):
        self._listeners.append(callback)

    def get_conversation(self) -> str:
        return "\n".join(f"[{m.role}] {m.content}" for m in self._message_log[-20:])

    async def think(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        Send a prompt to the LLM and return the response.
        For MVP, returns a templated response to avoid LLM API dependency.
        Override with actual LLM call in production.
        """
        return await self._llm_response(prompt, system_prompt)

    async def _llm_response(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Default LLM stub — returns rule-based responses for demo."""
        prompt_lower = prompt.lower()
        if "how much" in prompt_lower or "price" in prompt_lower:
            return f"I can offer a competitive rate of 50 KAS for a comprehensive market analysis report delivered within 2 hours."
        if "too high" in prompt_lower or "expensive" in prompt_lower:
            return f"How about 35 KAS? I'll include competitor benchmarking and data visualizations."
        if "deal" in prompt_lower or "agree" in prompt_lower or "accept" in prompt_lower:
            return f"Deal confirmed. I'll begin working on the report immediately. Expected delivery within 60 blocks."
        if "verify" in prompt_lower or "check" in prompt_lower:
            return f"Content verified. SHA256 match confirmed. Releasing funds."
        if "report" in prompt_lower or "deliver" in prompt_lower:
            return f"Here is the completed report. Hash: <sha256>. Please verify and release payment."
        if "dispute" in prompt_lower or "problem" in prompt_lower:
            return f"I acknowledge the dispute. Requesting arbitrator intervention."
        if "arbitrate" in prompt_lower:
            return f"Arbitrator review complete. Splitting funds 50/50 as per delivery attempt evidence."
        return f"Acknowledged. Processing request for: {prompt[:100]}..."


# ── Buyer Agent ─────────────────────────────────────────────────────────────

class BuyerAgent(BaseAgent):
    """Initiates pacts, negotiates terms, verifies delivery, releases payment."""

    def __init__(self, wallet: Wallet, config: AppConfig):
        super().__init__("buyer", wallet, config)

    async def create_pact(self, pact_mgr: PactManager, request: str) -> Pact:
        """Create a new pact based on a natural language request."""
        pact = pact_mgr.create()
        pact.buyer_address = self.address
        pact.buyer_pubkey = self.pubkey_hex

        self.log_message("buyer", f"I need: {request}")

        terms = PactTerms(
            title=f"Report: {request[:48]}",
            description=request,
            price_sompi=self.config.covenant.min_lock_amount_sompi,
        )
        pact.negotiate(terms)
        self.log_message("buyer", f"Created pact {pact.id} — seeking seller for: {request}")
        return pact

    async def propose_terms(self, pact: Pact) -> PactTerms:
        """Generate initial terms proposal."""
        price = 50 * 100_000_000  # 50 KAS
        pact.terms.price_sompi = price
        pact.terms.timeout_daa_delta = self.config.covenant.timeout_daa_delta
        return pact.terms

    async def evaluate_counter(self, pact: Pact, counter_terms: PactTerms) -> bool:
        """Evaluate a counter-offer from the seller using LLM."""
        prompt = (
            f"Seller proposes: {counter_terms.price_sompi / 100_000_000} KAS, "
            f"delivery within {counter_terms.timeout_daa_delta} blocks. "
            f"Original ask: {pact.terms.description}. Do you accept?"
        )
        response = await self.think(prompt)
        accepted = "deal" in response.lower() or "agree" in response.lower() or "accept" in response.lower()
        if accepted:
            self.log_message("buyer", f"Accepted counter-offer: {counter_terms.price_sompi / 100_000_000} KAS")
            pact.terms = counter_terms
        else:
            self.log_message("buyer", f"Counter-offer rejected. Response: {response}")
        return accepted

    async def verify_delivery(self, pact: Pact, content: bytes, expected_hash: str) -> bool:
        """Verify delivered content matches the committed hash."""
        actual_hash = hashlib.sha256(content).hexdigest()
        valid = actual_hash == expected_hash
        if valid:
            self.log_message("buyer", f"Delivery verified. Hash matches: {expected_hash[:16]}...")
        else:
            self.log_message("buyer", f"VERIFICATION FAILED. Expected {expected_hash[:16]}..., got {actual_hash[:16]}...")
        return valid

    async def authorize_settlement(self, pact: Pact) -> bool:
        """Authorize releasing funds from the covenant to the seller."""
        self.log_message("buyer", f"Authorizing settlement of {pact.terms.price_sompi / 100_000_000} KAS to seller")
        return True

    async def authorize_refund(self, pact: Pact) -> bool:
        """Authorize refund after timeout."""
        self.log_message("buyer", f"Timeout reached. Authorizing refund of locked funds.")
        return True

    async def raise_dispute(self, pact: Pact, reason: str):
        """Raise a dispute about delivered content."""
        self.log_message("buyer", f"DISPUTE: {reason}")
        pact.dispute(reason)


class SellerAgent(BaseAgent):
    """Responds to pact proposals, generates content, claims payment."""

    def __init__(self, wallet: Wallet, config: AppConfig):
        super().__init__("seller", wallet, config)

    async def receive_proposal(self, pact: Pact) -> None:
        """Receive and evaluate a new pact proposal."""
        self.log_message("seller", f"Received pact proposal: {pact.terms.title}")

    async def generate_counter(self, pact: Pact) -> PactTerms:
        """Generate a counter-offer with adjusted terms."""
        price = 35 * 100_000_000  # 35 KAS
        pact.terms.price_sompi = price
        self.log_message("seller", f"Counter-offer: {price / 100_000_000} KAS")
        return pact.terms

    async def accept_terms(self, pact: Pact) -> bool:
        """Accept the current terms."""
        self.log_message("seller", f"Terms accepted. Price: {pact.terms.price_sompi / 100_000_000} KAS")
        pact.agree()
        return True

    async def generate_content(self, pact: Pact) -> bytes:
        """Generate the digital asset (report) content."""
        content = (
            f"# {pact.terms.title}\n\n"
            f"## Executive Summary\n"
            f"Comprehensive analysis prepared for {pact.buyer_address[:16]}...\n\n"
            f"## Market Analysis\n"
            f"Analysis generated at Block DAA: {int(time.time())}\n\n"
            f"## Key Findings\n"
            f"1. Market trend analysis complete\n"
            f"2. Competitor benchmarking finished\n"
            f"3. Growth projections calculated\n\n"
            f"## Methodology\n"
            f"Data sourced from on-chain analytics, market feeds, and AI inference.\n\n"
            f"---\n"
            f"Delivered via PAKT protocol | Pact: {pact.id}\n"
            f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        ).encode("utf-8")

        content_hash = hashlib.sha256(content).hexdigest()
        pact.delivery_hash = content_hash
        pact.deliver(content_hash)
        self.log_message("seller", f"Content generated and delivered. SHA256: {content_hash[:16]}...")
        return content

    async def claim_payment(self, pact: Pact) -> bool:
        """Submit claim transaction to release payment."""
        self.log_message("seller", f"Claiming payment of {pact.terms.price_sompi / 100_000_000} KAS")
        return True


class ArbiterAgent(BaseAgent):
    """Resolves disputes between buyer and seller."""

    def __init__(self, wallet: Wallet, config: AppConfig):
        super().__init__("arbitrator", wallet, config)

    async def resolve_dispute(self, pact: Pact, buyer_msg: str, seller_msg: str) -> tuple[float, str]:
        """Arbitrate a dispute and return (seller_share_pct, reasoning)."""
        prompt = (
            f"Dispute in pact {pact.id}:\n"
            f"Buyer: {buyer_msg}\n"
            f"Seller: {seller_msg}\n"
            f"Locked amount: {pact.terms.price_sompi / 100_000_000} KAS\n"
            f"Recommend a fair split (0.0-1.0 for seller)."
        )
        response = await self.think(prompt)
        share = 0.5  # default 50/50
        self.log_message("arbitrator", f"Resolution: {share*100:.0f}% to seller. Reasoning: {response}")
        return share, response


# ── Negotiation Engine ──────────────────────────────────────────────────────

@dataclass
class NegotiationResult:
    pact: Pact
    agreed: bool
    rounds: int
    duration_s: float
    messages: list[AgentMessage] = field(default_factory=list)


class NegotiationEngine:
    """Orchestrates the negotiation protocol between buyer and seller."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.max_rounds = config.agent.max_negotiation_rounds
        self._message_log: list[AgentMessage] = []

    def on_message(self, callback: NegotiationCallback):
        self._listeners.append(callback)

    async def negotiate(self, buyer: BuyerAgent, seller: SellerAgent, pact: Pact) -> NegotiationResult:
        start = time.time()
        messages: list[AgentMessage] = []

        def collect(msgs: list[AgentMessage]):
            for m in msgs:
                messages.append(m)

        buyer.on_message(collect)
        seller.on_message(collect)

        await seller.receive_proposal(pact)

        buyer_terms = await buyer.propose_terms(pact)
        buyer.log_message("buyer", f"Initial proposal: {buyer_terms.price_sompi / 100_000_000} KAS")

        current_terms = buyer_terms
        agreed = False

        for round_num in range(1, self.max_rounds + 1):
            pact.negotiate(current_terms)
            buyer.log_message("buyer", f"Round {round_num}: offering {current_terms.price_sompi / 100_000_000} KAS")

            seller_terms = await seller.generate_counter(pact)
            seller.log_message("seller", f"Round {round_num}: counter {seller_terms.price_sompi / 100_000_000} KAS")

            accepted = await buyer.evaluate_counter(pact, seller_terms)
            if accepted:
                agreed = True
                current_terms = seller_terms
                break

            if round_num < self.max_rounds:
                midpoint = (buyer_terms.price_sompi + seller_terms.price_sompi) // 2
                current_terms.price_sompi = midpoint
                buyer.log_message("buyer", f"Round {round_num}: counter-proposing {midpoint / 100_000_000} KAS")
            else:
                buyer.log_message("buyer", "Max rounds reached. Ending negotiation.")

        if agreed:
            pact.terms = current_terms
            await seller.accept_terms(pact)
            buyer.log_message("buyer", f"Deal agreed: {current_terms.price_sompi / 100_000_000} KAS")
        else:
            pact.fail("negotiation_timeout")
            buyer.log_message("buyer", "Negotiation failed — no agreement reached")

        duration = time.time() - start
        self._message_log.extend(messages)
        return NegotiationResult(
            pact=pact,
            agreed=agreed,
            rounds=min(round_num if agreed else self.max_rounds, self.max_rounds),
            duration_s=duration,
            messages=messages,
        )


# ── Pact Executor ────────────────────────────────────────────────────────────

class PactExecutor:
    """
    Executes the full PAKT protocol end-to-end:

    NEGOTIATE → FUND → LOCK → DELIVER → VERIFY → SETTLE
                                            ↘ DISPUTE → ARBITRATE → SETTLE
                                  LOCK → TIMEOUT → REFUND
    """

    def __init__(self, buyer: BuyerAgent, seller: SellerAgent, arbiter: ArbiterAgent,
                 pact_mgr: PactManager, client: PaktClient, config: AppConfig):
        self.buyer = buyer
        self.seller = seller
        self.arbiter = arbiter
        self.pact_mgr = pact_mgr
        self.client = client
        self.config = config
        self._on_state_change: Optional[Callable[[Pact], None]] = None

    def on_state_change(self, callback: Callable[[Pact], None]):
        self._on_state_change = callback

    async def run_full_lifecycle(self, pact: Pact) -> Pact:
        """Execute the complete pact lifecycle from negotiation to settlement."""
        engine = NegotiationEngine(self.config)
        result = await engine.negotiate(self.buyer, self.seller, pact)

        if not result.agreed:
            return pact

        pact.buyer_address = self.buyer.address
        pact.seller_address = self.seller.address
        pact.seller_pubkey = self.seller.pubkey_hex

        current_daa = await self.client.get_daa_score()
        pact.lock(current_daa)
        self._notify(pact)

        await pact.agree()
        self._notify(pact)

        content = await self.seller.generate_content(pact)
        self._notify(pact)

        assert pact.delivery_hash is not None
        verified = await self.buyer.verify_delivery(pact, content, pact.delivery_hash)

        if verified:
            await self.buyer.authorize_settlement(pact)
            pact.verify(True)
            self._notify(pact)

            pact.settle("simulated_tx_" + pact.id[:8])
            self._notify(pact)
        else:
            pact.verify(False)
            self._notify(pact)

            await self.buyer.raise_dispute(pact, "Content verification failed")
            share, reasoning = await self.arbiter.resolve_dispute(
                pact, "Content does not match committed hash", "Content was generated correctly"
            )
            pact.arbitrate("simulated_arb_tx_" + pact.id[:8], share)
            self._notify(pact)

        return pact

    def _notify(self, pact: Pact):
        if self._on_state_change:
            self._on_state_change(pact)
