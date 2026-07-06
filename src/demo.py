"""
PAKT CLI Demo — Real-time terminal interface for the pact lifecycle.

Displays:
  - Agent conversation (buyer ↔ seller) in scrolling chat
  - Pact state transitions with on-chain DAA score
  - Covenant creation and transaction confirmations
  - Dispute resolution flow (if triggered)
  - Final settlement summary

Usage:
  python -m src.demo
  python -m src.demo --scenario dispute
  python -m src.demo --fast
"""

from __future__ import annotations

import asyncio
import json
import time
import sys
from pathlib import Path
from typing import Optional


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from src.config import AppConfig, CONFIG
from src.wallet import Wallet, WalletRegistry
from src.pact import Pact, PactManager, PactState, PactTerms
from src.agent import BuyerAgent, SellerAgent, ArbiterAgent, PactExecutor, NegotiationEngine, AgentMessage
from src.hash_utils import ContentCommitment


# ── ANSI Terminal Helpers ────────────────────────────────────────────────────

class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"

    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    INFO = "\033[94m"
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    DARK = "\033[90m"
    WHITE = "\033[97m"

    BGGREEN = "\033[102m"
    BGFAIL = "\033[101m"
    BGINFO = "\033[104m"
    BGWARN = "\033[103m"

    @staticmethod
    def state(s: str) -> str:
        colors = {
            "draft": Style.DARK,
            "negotiating": Style.WARNING,
            "agreed": Style.CYAN,
            "funding": Style.INFO,
            "locked": Style.PURPLE,
            "delivered": Style.INFO,
            "verified": Style.OKGREEN,
            "settled": Style.BGGREEN + Style.BOLD + Style.WHITE,
            "refunded": Style.WARNING,
            "dispute": Style.BGFAIL + Style.BOLD + Style.WHITE,
            "arbitrating": Style.BGWARN + Style.BOLD,
            "failed": Style.BGFAIL + Style.BOLD + Style.WHITE,
        }
        c = colors.get(s.lower(), Style.RESET)
        return f"{c}{s.upper()}{Style.RESET}"

    @staticmethod
    def role(r: str) -> str:
        roles = {
            "buyer": f"{Style.INFO}BUYER{Style.RESET}",
            "seller": f"{Style.OKGREEN}SELLER{Style.RESET}",
            "arbitrator": f"{Style.WARNING}ARB{Style.RESET}",
            "system": f"{Style.PURPLE}SYSTEM{Style.RESET}",
        }
        return roles.get(r.lower(), r.upper())

    @staticmethod
    def kas(amount_sompi: int) -> str:
        kas = amount_sompi / 100_000_000
        return f"{Style.WARNING}{kas:.2f} KAS{Style.RESET}"

    @staticmethod
    def hash(h: str, n: int = 12) -> str:
        return f"{Style.DIM}{h[:n]}...{Style.RESET}"


HEADER = f"""
{Style.BOLD}{Style.CYAN}
  ╔══════════════════════════════════════════════════════════════╗
  ║                    PAKT PROTOCOL v0.1                        ║
  ║     AI Agent Pact Engine · Kaspa Covenant Layer             ║
  ╚══════════════════════════════════════════════════════════════╝{Style.RESET}
"""


# ── Terminal Display ────────────────────────────────────────────────────────

class DemoDisplay:
    """Real-time terminal UI for the PAKT demo."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._messages: list[str] = []
        self._pact_states: dict[str, str] = {}

    def clear(self):
        print("\033[2J\033[H", end="")

    def header(self):
        print(HEADER)

    def divider(self, char: str = "─", width: int = 60):
        print(f"{Style.DIM}{char * width}{Style.RESET}")

    def log(self, role: str, msg: str, style: str = ""):
        line = f"  {Style.role(role)} {style}{msg}{Style.RESET}"
        self._messages.append(line)
        print(line)

    def state_box(self, pact: Pact):
        s = pact.state.value
        self._pact_states[pact.id] = s
        print(f"\n  {Style.BOLD}PACT {Style.state(s)}{Style.RESET}")
        print(f"  {Style.DIM}ID:{Style.RESET} {pact.id[:16]}...  "
              f"{Style.DIM}Price:{Style.RESET} {Style.kas(pact.terms.price_sompi)}  "
              f"{Style.DIM}State:{Style.RESET} {Style.state(s)}")

    def tx_box(self, label: str, txid: str, status: str = "pending"):
        colors = {"confirmed": Style.OKGREEN, "pending": Style.WARNING, "failed": Style.FAIL}
        c = colors.get(status, Style.DIM)
        print(f"  {Style.DIM}{label}:{Style.RESET} {Style.hash(txid)}  {c}[{status.upper()}]{Style.RESET}")

    def chat_bubble(self, msg: AgentMessage):
        role_tag = Style.role(msg.role)
        time_str = time.strftime("%H:%M:%S", time.gmtime(msg.timestamp))
        print(f"  {Style.DIM}[{time_str}]{Style.RESET} {role_tag} {msg.content}")

    def negotiation_log(self, messages: list[AgentMessage]):
        self.divider()
        print(f"  {Style.BOLD}Agent Negotiation Log{Style.RESET}\n")
        for msg in messages:
            self.chat_bubble(msg)
            time.sleep(0.3)

    def lifecycle_diagram(self, pact: Pact):
        flow = {
            PactState.DRAFT: "○ DRAFT",
            PactState.NEGOTIATING: "○ NEGOTIATING",
            PactState.AGREED: "○ AGREED",
            PactState.FUNDING: "○ FUNDING",
            PactState.LOCKED: "○ LOCKED",
            PactState.DELIVERED: "○ DELIVERED",
            PactState.VERIFIED: "○ VERIFIED",
            PactState.SETTLED: "● SETTLED",
            PactState.REFUNDING: "○ REFUNDING",
            PactState.REFUNDED: "● REFUNDED",
            PactState.DISPUTE: "⚠ DISPUTE",
            PactState.ARBITRATING: "○ ARBITRATING",
            PactState.FAILED: "✗ FAILED",
            PactState.EXPIRED: "✗ EXPIRED",
        }
        current = flow.get(pact.state, "○ ???")
        self.divider()
        print(f"  {Style.BOLD}Lifecycle{Style.RESET}  {current}")
        self.divider()

    def summary_table(self, pact: Pact, duration_s: float):
        self.divider("=")
        print(f"  {Style.BOLD}{Style.OKGREEN}SETTLEMENT SUMMARY{Style.RESET}\n")
        print(f"  {Style.DIM}Pact ID:{Style.RESET}        {pact.id}")
        print(f"  {Style.DIM}State:{Style.RESET}          {Style.state(pact.state.value)}")
        print(f"  {Style.DIM}Price:{Style.RESET}          {Style.kas(pact.terms.price_sompi)}")
        print(f"  {Style.DIM}Buyer:{Style.RESET}          {pact.buyer_address[:20]}...")
        print(f"  {Style.DIM}Seller:{Style.RESET}         {pact.seller_address[:20]}...")
        print(f"  {Style.DIM}Covenant:{Style.RESET}       {Style.hash(pact.covenant_address, 20)}")
        print(f"  {Style.DIM}Funding TX:{Style.RESET}     {Style.hash(pact.funding_txid or 'N/A')}")
        print(f"  {Style.DIM}Settlement TX:{Style.RESET}  {Style.hash(pact.claim_txid or 'N/A')}")
        print(f"  {Style.DIM}Duration:{Style.RESET}       {duration_s:.1f}s")
        print(f"  {Style.DIM}Rounds:{Style.RESET}         {len(pact.history)}")
        self.divider("=")

    def wait(self, message: str = "Press Enter to continue..."):
        input(f"\n  {Style.DIM}{message}{Style.RESET} ")


# ── Demo Scenarios ──────────────────────────────────────────────────────────

class DemoScenario:
    HAPPY_PATH = "happy"
    DISPUTE = "dispute"
    TIMEOUT = "timeout"


async def run_demo(scenario: str = DemoScenario.HAPPY_PATH, fast: bool = False):
    config = AppConfig.default()
    display = DemoDisplay(config)
    pact_mgr = PactManager()

    display.clear()
    display.header()

    display.log("system", "Initializing PAKT protocol...")
    display.log("system", f"Network: {config.network.id}")
    display.log("system", "Generating agent wallets...")

    wallets = WalletRegistry()
    wallets.generate_all()
    status = wallets.status()
    for role, info in status.items():
        display.log("system", f"  {role}: {info['address'][:20]}...  (pubkey: {info['pubkey'][:16]}...)")

    buyer = BuyerAgent(wallets.buyer, config)
    seller = SellerAgent(wallets.seller, config)
    arbiter = ArbiterAgent(wallets.arbitrator, config)

    display.divider()
    display.log("system", f"{Style.BOLD}Creating Pact{Style.RESET}")

    request = "Generate a comprehensive market analysis report for Kaspa ecosystem Q3 2026"
    pact = await buyer.create_pact(pact_mgr, request)

    pact.seller_address = seller.address
    pact.seller_pubkey = seller.pubkey_hex

    executor = PactExecutor(buyer, seller, arbiter, pact_mgr, None, config)

    pact.buyer_address = buyer.address
    pact.seller_address = seller.address

    engine = NegotiationEngine(config)
    display.divider()
    display.log("system", f"{Style.BOLD}Phase 1: Negotiation{Style.RESET}")

    negotiation = await engine.negotiate(buyer, seller, pact)
    display.negotiation_log(negotiation.messages)

    if not negotiation.agreed:
        display.log("system", f"{Style.FAIL}Negotiation failed — no agreement reached{Style.RESET}")
        display.summary_table(pact, negotiation.duration_s)
        return

    display.log("system", f"{Style.OKGREEN}Deal agreed!{Style.RESET} {Style.kas(pact.terms.price_sompi)}")
    display.state_box(pact)

    display.divider()
    display.log("system", f"{Style.BOLD}Phase 2: Covenant Creation{Style.RESET}")

    pact.funding_txid = f"sim_fund_{pact.id[:8]}"
    covenant_addr = f"kaspatest:pakt_{pact.id[:16]}"
    pact.covenant_address = covenant_addr
    pact.fund(pact.funding_txid, covenant_addr)
    display.tx_box("Funding TX", pact.funding_txid, "pending")
    time.sleep(0.5)
    pact.lock(42000)
    display.tx_box("Funding TX", pact.funding_txid, "confirmed")
    display.state_box(pact)

    if scenario == DemoScenario.TIMEOUT:
        display.divider()
        display.log("system", f"{Style.BOLD}Phase 3: Timeout & Refund{Style.RESET}")
        await asyncio.sleep(0.5)
        for _ in range(3):
            daa = 42000 + _
            pact.lock(daa)
        pact.expire()
        pact.refund(f"sim_refund_{pact.id[:8]}")
        display.tx_box("Refund TX", pact.refund_txid or "N/A", "confirmed")
        display.summary_table(pact, negotiation.duration_s + 3)
        return

    display.divider()
    display.log("system", f"{Style.BOLD}Phase 3: Delivery{Style.RESET}")

    content = await seller.generate_content(pact)
    display.log("seller", f"Content delivered. Hash: {pact.delivery_hash[:20]}...")
    display.state_box(pact)

    if scenario == DemoScenario.DISPUTE:
        display.divider()
        display.log("system", f"{Style.BOLD}{Style.FAIL}Phase 4: Verification Failed — Dispute{Style.RESET}")
        pact.verify(False, reason="Content hash mismatch: expected 2a3b... got 9c8d...")
        display.state_box(pact)
        display.log("system", f"Escalating to arbitrator...")
        time.sleep(0.5)
        share, reason = await arbiter.resolve_dispute(pact, "Hash mismatch", "Content generated correctly")
        pact.arbitrate(f"sim_arb_{pact.id[:8]}", share)
        display.log("arbitrator", f"Split: {share*100:.0f}% seller")
        display.tx_box("Arbitration TX", pact.claim_txid or "N/A", "confirmed")
    else:
        display.divider()
        display.log("system", f"{Style.BOLD}Phase 4: Verification & Settlement{Style.RESET}")
        verified = await buyer.verify_delivery(pact, content, pact.delivery_hash or "")
        if verified:
            pact.verify(True)
            pact.settle(f"sim_settle_{pact.id[:8]}")
            display.tx_box("Settlement TX", pact.claim_txid or "N/A", "confirmed")
            display.log("system", f"{Style.OKGREEN}{Style.BOLD}PAYMENT RELEASED{Style.RESET} {Style.kas(pact.terms.price_sompi)} → seller")

    display.summary_table(pact, negotiation.duration_s + 5)

    return pact.metadata


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PAKT Protocol Demo")
    parser.add_argument("--scenario", choices=["happy", "dispute", "timeout"], default="happy")
    parser.add_argument("--fast", action="store_true", help="Skip delays")
    args = parser.parse_args()

    asyncio.run(run_demo(scenario=args.scenario, fast=args.fast))


if __name__ == "__main__":
    main()
