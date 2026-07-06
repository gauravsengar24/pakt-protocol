"""
PAKT Protocol — Hugging Face Spaces Web Interface.

Streamlit-based interactive demo. Judges run scenarios and inspect the
full covenant lifecycle — negotiation, locking, delivery, settlement,
dispute, and refund — all from the browser.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import AppConfig
from src.wallet import WalletRegistry
from src.pact import Pact, PactManager, PactState
from src.agent import BuyerAgent, SellerAgent, ArbiterAgent, NegotiationEngine, AgentMessage

# ── Page ─────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="PAKT Protocol", page_icon="🤝",
                   layout="wide", initial_sidebar_state="expanded")

# ── CSS ──────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .stApp { background-color: #0b0b16; }
    .glass-card {
        background: linear-gradient(135deg, rgba(255,255,255,0.04), rgba(5,5,8,0.6));
        backdrop-filter: blur(25px) saturate(210%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        padding: 20px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5);
        margin-bottom: 12px;
    }
    .metric-label { color: #78788a; font-size: 0.8em; }
    .metric-value { color: #f0f0f5; font-weight: 600; }
    .live-dot {
        display: inline-block; width: 8px; height: 8px;
        background: #22c55e; border-radius: 50%;
        animation: pulse 1.5s ease-in-out infinite;
        margin-right: 6px;
    }
    @keyframes pulse {
        0% { opacity: 0.4; }
        50% { opacity: 1; }
        100% { opacity: 0.4; }
    }
</style>
""", unsafe_allow_html=True)


# ── Session State ────────────────────────────────────────────────────────────

if "msgs" not in st.session_state:
    st.session_state.msgs: list[AgentMessage] = []
    st.session_state.pact: Pact | None = None
    st.session_state.pact_mgr = PactManager()
    st.session_state.wallets = WalletRegistry()
    st.session_state.wallets.generate_all()
    st.session_state.scenario = "happy"
    st.session_state.has_run = False


# ── Core Demo ────────────────────────────────────────────────────────────────

def run_demo_sync(scenario: str) -> tuple[list[AgentMessage], Pact | None]:
    """Run the full demo lifecycle synchronously (wraps async)."""

    async def _run() -> tuple[list[AgentMessage], Pact | None]:
        config = AppConfig.default()
        pact_mgr = PactManager()
        wallets = WalletRegistry()
        wallets.generate_all()

        buyer = BuyerAgent(wallets.buyer, config)
        seller = SellerAgent(wallets.seller, config)
        arbiter = ArbiterAgent(wallets.arbitrator, config)

        msgs: list[AgentMessage] = []

        def log(role: str, text: str):
            msgs.append(AgentMessage(role=role, content=text))

        log("system", f"🤖 PAKT Protocol initializing on Kaspa Testnet-12")
        log("system", f"• Buyer: `{wallets.buyer.address[:20]}...`")
        log("system", f"• Seller: `{wallets.seller.address[:20]}...`")

        request = "Generate a comprehensive market analysis report for Kaspa ecosystem Q3 2026"
        pact = pact_mgr.create()
        pact.buyer_address = wallets.buyer.address
        pact.buyer_pubkey = wallets.buyer.pubkey_hex or ""
        pact.seller_address = wallets.seller.address
        pact.seller_pubkey = wallets.seller.pubkey_hex or ""

        log("buyer", f"📋 **Request:** {request}")

        engine = NegotiationEngine(config)
        result = await engine.negotiate(buyer, seller, pact)
        for m in result.messages:
            if m not in msgs:
                msgs.append(m)

        if not result.agreed:
            log("system", "❌ Negotiation failed")
            return msgs, None

        log("system", f"✅ **Deal agreed:** {pact.terms.price_sompi / 100_000_000:.2f} KAS")

        # Covenant
        pact.funding_txid = f"sim_fund_{pact.id[:8]}"
        pact.covenant_address = f"kaspatest:pakt_{pact.id[:16]}"
        pact.fund(pact.funding_txid, pact.covenant_address)
        pact.lock(42000)
        log("system", f"🔗 **Covenant funded** → `{pact.covenant_address[:24]}...`")
        log("system", f"📦 Funding TX: `{pact.funding_txid[:20]}...`")

        if scenario == "timeout":
            pact.expire()
            pact.refund(f"sim_refund_{pact.id[:8]}")
            log("system", "⏰ **Timeout** — DAA score exceeded threshold")
            log("system", f"💰 **Refund** → buyer reclaims {pact.terms.price_sompi / 100_000_000:.2f} KAS")
            log("system", f"📦 Refund TX: `{pact.refund_txid[:20]}...`")
            return msgs, pact

        content = await seller.generate_content(pact)
        log("seller", f"📄 **Content delivered** SHA256: `{pact.delivery_hash[:20]}...`")

        if scenario == "dispute":
            pact.verify(False, reason="Content hash mismatch")
            log("buyer", "❌ **Verification failed** — hash does not match covenant commitment")
            log("system", "⚖️ **Escalating to arbitrator...**")
            share, _ = await arbiter.resolve_dispute(pact, "Hash mismatch", "Content generated correctly")
            pact.arbitrate(f"sim_arb_{pact.id[:8]}", share)
            log("arbitrator", f"⚖️ **Resolution**: {share*100:.0f}% of locked funds → seller")
            log("system", f"✅ **Arbitrated settlement** TX: `{pact.claim_txid[:20]}...`")
            return msgs, pact

        # Happy path
        verified = await buyer.verify_delivery(pact, content, pact.delivery_hash or "")
        if verified:
            pact.verify(True)
            pact.settle(f"sim_settle_{pact.id[:8]}")
            amount = pact.terms.price_sompi / 100_000_000
            log("buyer", f"✅ **Content verified** — SHA256 matches commitment")
            log("system", f"💰 **PAYMENT RELEASED**: {amount:.2f} KAS → seller")
            log("system", f"📦 Settlement TX: `{pact.claim_txid[:20]}...`")
        else:
            pact.verify(False)

        return msgs, pact

    return asyncio.run(_run())


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("""
    <div style="text-align:center;margin-bottom:20px;">
        <h2 style="color:#60cdff;margin:0;">🤝 PAKT</h2>
        <p style="color:#78788a;font-size:0.8em;margin:0;">Protocol v0.1</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    scenario = st.selectbox(
        "Scenario",
        ["happy", "dispute", "timeout"],
        format_func=lambda x: {"happy": "✅ Happy Path — Settle",
                              "dispute": "⚖️ Dispute — Arbitrate",
                              "timeout": "⏰ Timeout — Refund"}[x],
        key="scenario_sel",
    )

    run = st.button("▶ Run", type="primary", use_container_width=True)
    if st.button("↺ Reset", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    st.markdown("---")
    st.markdown("### 👤 Wallets")
    w = st.session_state.wallets
    for role in ("buyer", "seller", "arbitrator"):
        addr = getattr(w, role).address[:16] if getattr(w, role) else "—"
        st.markdown(f"**{role.title()}:** `{addr}...`")

    st.markdown("---")
    st.markdown('<span class="live-dot"></span> **Kaspa Testnet-12**',
                unsafe_allow_html=True)


# ── Main Panel ───────────────────────────────────────────────────────────────

st.markdown("""
<div style="text-align:center;margin-bottom:24px;">
    <h1 style="color:#f0f0f5;margin:0;font-size:2.2em;">
        🤝 <span style="color:#60cdff;">PAKT</span> Protocol
    </h1>
    <p style="color:#78788a;margin:4px 0 0 0;">
        AI Agent Pact Engine · Kaspa Covenant Layer
    </p>
</div>
""", unsafe_allow_html=True)

# ── Run Logic ────────────────────────────────────────────────────────────────

if run:
    st.session_state.has_run = True
    st.session_state.scenario = scenario
    with st.spinner("Executing PAKT protocol..."):
        msgs, pact = run_demo_sync(scenario)
    st.session_state.msgs = msgs
    st.session_state.pact = pact
    st.rerun()

pact = st.session_state.pact
msgs = st.session_state.msgs

# ── Status Cards ─────────────────────────────────────────────────────────────

c1, c2, c3 = st.columns([1, 1, 1])

with c1:
    label = "READY"
    color = "#78788a"
    if pact:
        label = pact.state.value.upper()
        color = {"settled": "#22c55e", "refunded": "#eab308",
                 "dispute": "#ef4444", "failed": "#ef4444",
                 "settled (arbitrated)": "#a855f7"}.get(label, "#60cdff")
    st.markdown(
        f'<div class="glass-card">'
        f'<div class="metric-label">PACT STATE</div>'
        f'<div class="metric-value" style="color:{color};">{label}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

with c2:
    if pact:
        amt = pact.terms.price_sompi / 100_000_000
        st.markdown(
            f'<div class="glass-card">'
            f'<div class="metric-label">COVENANT</div>'
            f'<div class="metric-value">{amt:.2f} KAS</div>'
            f'<div class="metric-label">{pact.covenant_address[:24]}...</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="glass-card">'
            f'<div class="metric-label" style="color:#78788a;">COVENANT</div>'
            f'<div class="metric-value" style="color:#78788a;">—</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

with c3:
    if pact:
        f = pact.funding_txid[:16] if pact.funding_txid else "—"
        c = pact.claim_txid[:16] if pact.claim_txid else "—"
        st.markdown(
            f'<div class="glass-card">'
            f'<div class="metric-label">TRANSACTIONS</div>'
            f'<div class="metric-value" style="font-size:0.75em;">Funding: {f}...</div>'
            f'<div class="metric-value" style="font-size:0.75em;">Claim:   {c}...</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="glass-card">'
            f'<div class="metric-label" style="color:#78788a;">TRANSACTIONS</div>'
            f'<div class="metric-value" style="color:#78788a;">—</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

# ── Chat + Info ──────────────────────────────────────────────────────────────

col_chat, col_info = st.columns([3, 2])

with col_chat:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown("### 💬 Agent Conversation")

    chat = st.container(height=440)
    with chat:
        for msg in msgs:
            role_map = {"buyer": "Buyer Agent", "seller": "Seller Agent",
                        "arbitrator": "Arbitrator", "system": "PAKT Protocol"}
            with st.chat_message(msg.role if msg.role in ("buyer", "seller", "system") else "assistant"):
                st.markdown(f"**{role_map.get(msg.role, msg.role)}**  \n{msg.content}")

    st.markdown('</div>', unsafe_allow_html=True)

with col_info:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown("### 📋 Pact Details")

    if pact:
        for label, val in [
            ("ID", pact.id[:16] + "..."),
            ("State", pact.state.value.upper()),
            ("Price", f"{pact.terms.price_sompi / 100_000_000:.2f} KAS"),
            ("Buyer", pact.buyer_address[:20] + "..."),
            ("Seller", pact.seller_address[:20] + "..."),
            ("Covenant", (pact.covenant_address[:24] + "...") if pact.covenant_address else "—"),
            ("Funding TX", (pact.funding_txid[:24] + "...") if pact.funding_txid else "—"),
            ("Claim TX", (pact.claim_txid[:24] + "...") if pact.claim_txid else "—"),
        ]:
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
                f'border-bottom:1px solid rgba(255,255,255,0.04);">'
                f'<span class="metric-label">{label}</span>'
                f'<span class="metric-value" style="font-size:0.85em;">{val}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("### 🔄 Lifecycle")
        states = ["draft", "negotiating", "agreed", "funding", "locked",
                  "delivered", "verified", "settled"]
        idx = next((i for i, s in enumerate(states) if s == pact.state.value), -1)
        cols = st.columns(len(states))
        for i, s in enumerate(states):
            done = i <= idx
            cols[i].markdown(
                f'<div style="text-align:center;font-size:0.65em;">'
                f'<span style="color:{"#22c55e" if done else "#1e1e2e"};">'
                f'{"●" if done else "○"}</span><br>'
                f'<span style="color:#78788a;">{s[:4]}</span></div>',
                unsafe_allow_html=True,
            )

        if pact.state in (PactState.SETTLED, PactState.REFUNDED):
            st.markdown("---")
            amt = pact.terms.price_sompi / 100_000_000
            st.markdown(
                f'<div style="text-align:center;padding:8px;'
                f'background:rgba(34,197,94,0.1);border-radius:12px;">'
                f'<span style="color:#22c55e;font-size:1.4em;font-weight:700;">'
                f'{amt:.2f} KAS</span>'
                f'<br><span style="color:#78788a;">settled via Kaspa covenant</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown("Run a scenario above to see pact details.")

    st.markdown('</div>', unsafe_allow_html=True)

# ── Footer ───────────────────────────────────────────────────────────────────

st.markdown("""
<div style="text-align:center;color:#78788a;font-size:0.75em;padding:20px 0;">
    PAKT Protocol v0.1 · Built on Kaspa Testnet-12 · 
    <a href="https://github.com/gauravsengar24/pakt-protocol" 
       style="color:#60cdff;text-decoration:none;">GitHub</a>
</div>
""", unsafe_allow_html=True)
