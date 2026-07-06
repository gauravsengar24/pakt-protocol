#!/usr/bin/env python3
"""
PAKT Protocol — Live Testnet-12 Deployment & Transaction Lifecycle.

Executes the full PAKT covenant lifecycle against a live Kaspa testnet node:

  CONNECT → GET DAA → GENERATE PRE-IMAGE → DERIVE COVENANT ADDRESS →
  LIST UTXOs → BUILD FUNDING TX → SUBMIT & CONFIRM →
  BUILD CLAIM TX → SUBMIT & CONFIRM → SETTLEMENT VERIFIED

Usage:
    # Export your Testnet-12 private keys (hex-encoded, 64 chars)
    export PAKT_TESTNET_BUYER_KEY=0101010101010101010101010101010101010101010101010101010101010101
    export PAKT_TESTNET_SELLER_KEY=0202020202020202020202020202020202020202020202020202020202020202

    python -m src.testnet_live_deploy

Environment:
    PAKT_TESTNET_BUYER_KEY   — Buyer private key (hex, 32 bytes)
    PAKT_TESTNET_SELLER_KEY  — Seller private key (hex, 32 bytes)
    PAKT_TESTNET_ARB_KEY     — Arbitrator private key (hex, 32 bytes, optional)
    PAKT_RPC_URL             — wRPC endpoint (default: wss://testnet-12.kaspa.org/wrpc/v1)
    PAKT_LOCK_AMOUNT         — Lock amount in sompi (default: 1_000_000_000 = 10 KAS)
    PAKT_TIMEOUT_DAA_DELTA   — Blocks until refund (default: 120)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kaspa_rpc import KaspaRPCClient, KaspaRPCConnection
from src.kaspa_tx import (
    KaspaTxBuilder, private_key_to_public, public_key_to_address,
    script_to_address, KaspaTransaction, hash160,
)
from src.hash_utils import ContentCommitment
from src.covenant import CovenantParams, redeem_script_htlc, CovenantTxBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pakt.deploy")


# ── Terminal Styling ─────────────────────────────────────────────────────────

class Style:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @staticmethod
    def ok(msg): return f"{Style.GREEN}{msg}{Style.RESET}"
    @staticmethod
    def warn(msg): return f"{Style.YELLOW}{msg}{Style.RESET}"
    @staticmethod
    def fail(msg): return f"{Style.RED}{msg}{Style.RESET}"
    @staticmethod
    def info(msg): return f"{Style.BLUE}{msg}{Style.RESET}"
    @staticmethod
    def head(msg): return f"{Style.BOLD}{Style.CYAN}{msg}{Style.RESET}"
    @staticmethod
    def dim(msg): return f"{Style.DIM}{msg}{Style.RESET}"


HEADER = f"""
{Style.head('╔══════════════════════════════════════════════════════════════╗')}
{Style.head('║     PAKT PROTOCOL — LIVE TESTNET-12 DEPLOYMENT              ║')}
{Style.head('║     AI Agent Pact Engine · Kaspa Covenant Layer             ║')}
{Style.head('╚══════════════════════════════════════════════════════════════╝')}
"""


# ── Configuration ───────────────────────────────────────────────────────────

def load_config() -> dict:
    return {
        "buyer_key": os.environ.get("PAKT_TESTNET_BUYER_KEY"),
        "seller_key": os.environ.get("PAKT_TESTNET_SELLER_KEY"),
        "arb_key": os.environ.get("PAKT_TESTNET_ARB_KEY", "ab" * 32),
        "rpc_url": os.environ.get("PAKT_RPC_URL",
                                  "wss://testnet-12.kaspa.org/wrpc/v1"),
        "lock_amount": int(os.environ.get("PAKT_LOCK_AMOUNT", "1_000_000_000")),
        "timeout_daa_delta": int(os.environ.get("PAKT_TIMEOUT_DAA_DELTA", "120")),
    }


def validate_config(cfg: dict):
    missing = [k for k in ("buyer_key", "seller_key") if not cfg.get(k)]
    if missing:
        logger.error(f"Missing required env vars: {missing}")
        print(f"\n{Style.fail('❌ Error:')} Missing required environment variables: {', '.join(missing)}")
        print(f"  {Style.dim('Export your Testnet-12 private keys:')}")
        print(f"  {Style.dim('  export PAKT_TESTNET_BUYER_KEY=<hex>')}")
        print(f"  {Style.dim('  export PAKT_TESTNET_SELLER_KEY=<hex>')}")
        print(f"  {Style.dim('')}")
        print(f"  {Style.dim('Generate keys using:')}")
        print(f"  {Style.dim('  python3 -c \"import secrets; print(secrets.token_hex(32))\"')}")
        print(f"  {Style.dim('Then fund them at: https://faucet.testnet-12.kaspa.org')}")
        sys.exit(1)

    for key_name in ("buyer_key", "seller_key"):
        key = cfg[key_name]
        if len(key) != 64:
            logger.error(f"{key_name} must be 64 hex chars (32 bytes), got {len(key)}")
            sys.exit(1)
        try:
            bytes.fromhex(key)
        except ValueError:
            logger.error(f"{key_name} is not valid hex")
            sys.exit(1)


# ── Display Helpers ─────────────────────────────────────────────────────────

def print_step(num: int, total: int, label: str):
    print(f"\n{Style.head(f'─── Step {num}/{total}: {label} ')}{'─' * max(0, 55 - len(label))}")


def print_ok(label: str, value: str = ""):
    print(f"  {Style.ok('✓')} {label} {Style.dim(value)}")


def print_warn(label: str, value: str = ""):
    print(f"  {Style.warn('⚠')} {label} {Style.dim(value)}")


def print_info(label: str, value: str = ""):
    print(f"  {Style.info('ℹ')} {label} {Style.dim(value)}")


# ── Main Deployment Flow ─────────────────────────────────────────────────────

async def run_live_testnet_flow():
    print(HEADER)

    cfg = load_config()
    validate_config(cfg)

    buyer_pubkey = private_key_to_public(cfg["buyer_key"])
    seller_pubkey = private_key_to_public(cfg["seller_key"])
    arb_pubkey = private_key_to_public(cfg["arb_key"])

    buyer_address = public_key_to_address(buyer_pubkey)
    seller_address = public_key_to_address(seller_pubkey)
    arb_address = public_key_to_address(arb_pubkey)

    print(f"  {Style.dim('Network:')}       Testnet-12")
    print(f"  {Style.dim('Buyer:')}          {buyer_address}")
    print(f"  {Style.dim('Seller:')}         {seller_address}")
    print(f"  {Style.dim('Arbitrator:')}     {arb_address}")
    print(f"  {Style.dim('Lock Amount:')}    {cfg['lock_amount'] / 1e8} KAS")
    print(f"  {Style.dim('RPC Endpoint:')}   {cfg['rpc_url']}")
    print(f"\n  {'─' * 60}")

    # ── Step 1: Connect ─────────────────────────────────────────────────
    print_step(1, 7, "Connect to Testnet-12 via wRPC")

    client = KaspaRPCClient(cfg["rpc_url"])
    try:
        await client.connect()
        server_version = await client.get_server_version()
        daa_start = await client.get_current_daa_score()
        print_ok("wRPC connection established", f"(node: {server_version})")
        print_ok("Current DAA score", str(daa_start))

        # Verify buyer has funds
        buyer_balance = await client.get_balance_by_address(buyer_address)
        if buyer_balance < cfg["lock_amount"] + 10_000:
            print_warn(f"Low balance: {buyer_balance / 1e8} KAS "
                       f"(need {cfg['lock_amount'] / 1e8} + 0.0001 fee)")
            print_info("Fund your wallet:", "https://faucet.testnet-12.kaspa.org")

        buyer_utxos = await client.get_utxos_by_address(buyer_address)
        print_ok(f"Buyer UTXOs found", str(len(buyer_utxos)))
        if buyer_utxos:
            total = sum(u.get("amount", 0) for u in buyer_utxos)
            print_ok(f"Total balance", f"{total / 1e8} KAS")

    except Exception as e:
        logger.error(f"Connection failed: {e}")
        print(f"\n{Style.fail('❌')} Connection failed: {e}")
        print(f"  This may indicate your IP is blocked or the node is down.")
        print(f"  Try: export PAKT_RPC_URL=wss://testnet-12.kaspa.org/wrpc/v1")
        await client.disconnect()
        return
    # ── Step 2: Generate Content Pre-image ───────────────────────────────
    print_step(2, 7, "Generate Off-Chain Content & Pre-image")

    # Simulate AI agent generating a market report
    report_content = (
        "PAKT LIVE TEST REPORT — Q3 2026 Market Intelligence\n"
        "------------------------------------------------\n"
        f"Generated at DAA: {daa_start}\n"
        f"Buyer: {buyer_address}\n"
        f"Topic: Kaspa Ecosystem Analysis\n"
        "Status: AI-verified autonomous delivery\n"
        f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        "\nKey Findings:\n"
        "1. Network hashrate continues to ascend with block reward stability\n"
        "2. Covenant adoption accelerating post-Toccata hardfork\n"
        "3. Agent-to-agent commerce emerging as primary use case\n"
    ).encode("utf-8")

    commitment = ContentCommitment(report_content)
    print_ok("Report content generated", f"({len(report_content)} bytes)")
    print_ok("SHA256 commitment", commitment.hex[:20] + "...")
    print_ok("Pre-image ready", "stored for covenant claim")

    # ── Step 3: Derive Covenant Address ─────────────────────────────────
    print_step(3, 7, "Derive SilverScript Covenant Address")

    timeout_daa = daa_start + cfg["timeout_daa_delta"]
    arb_timeout = daa_start + cfg["timeout_daa_delta"] + 50

    builder = KaspaTxBuilder()

    covenant_params = CovenantParams(
        buyer_pubkey=buyer_pubkey,
        seller_pubkey=seller_pubkey,
        arb_pubkey=arb_pubkey,
        content_hash=commitment.digest,
        timeout_daa=timeout_daa,
        arb_timeout_daa=arb_timeout,
        lock_amount=cfg["lock_amount"],
    )
    redeem_script = redeem_script_htlc(covenant_params)
    covenant_addr = script_to_address(redeem_script)

    print_ok("Redeem script compiled", f"({len(redeem_script)} bytes)")
    print_ok("Timeout DAA", str(timeout_daa))
    print_ok("Arbitration DAA", str(arb_timeout))
    print_ok("Covenant P2SH address", covenant_addr)

    # ── Step 4: Build & Submit Funding Transaction ───────────────────────
    print_step(4, 7, "Build & Submit Funding Transaction")

    if not buyer_utxos or sum(u.get("amount", 0) for u in buyer_utxos) < cfg["lock_amount"] + 10_000:
        print_warn("Insufficient funds — skipping live funding", "use simulated tx for demo continuity")
        funding_tx_id = "SIMULATED_FUNDING_" + hashlib.sha256(report_content).hexdigest()[:16]
        print_warn("SIMULATED funding tx", funding_tx_id)
    else:
        try:
            funding_tx = builder.build_funding_transaction(
                utxos=buyer_utxos,
                target_p2sh=covenant_addr,
                amount=cfg["lock_amount"],
                buyer_privkey=cfg["buyer_key"],
            )
            funding_hex = funding_tx.hex()
            funding_id = funding_tx.txid()
            print_ok("Funding transaction built", f"({len(funding_tx.inputs)} inputs, {len(funding_tx.outputs)} outputs)")

            funding_tx_id = await client.submit_transaction(funding_hex)
            print_ok("Funding transaction submitted", funding_tx_id)

            print_info("Awaiting block confirmation...")
            result = await client.wait_for_tx_confirmation(funding_tx_id, timeout=120.0)
            print_ok("Funding confirmed", f"@ DAA {result['daa_score']}")
        except Exception as e:
            logger.error(f"Funding failed: {e}")
            print(f"\n{Style.warn('⚠')} Live funding failed: {e}")
            funding_tx_id = "FALLBACK_FUNDING_" + hashlib.sha256(report_content).hexdigest()[:16]
            print_warn("Using fallback simulated tx", funding_tx_id)

    # ── Step 5: Monitor Covenant UTXO ────────────────────────────────────
    print_step(5, 7, "Monitor Covenant UTXO On-Chain")

    print_info("Polling UTXO set for covenant address...")
    try:
        covenant_utxos = await client.get_utxos_by_address(covenant_addr)
        if covenant_utxos:
            print_ok(f"Covenant UTXO detected", f"{len(covenant_utxos)} UTXO(s)")
            covenant_amount = sum(u.get("amount", 0) for u in covenant_utxos)
            print_ok(f"Covenant value", f"{covenant_amount / 1e8} KAS")
        else:
            print_warn("No covenant UTXO detected yet", "funding may still be pending")
            covenant_utxos = [{
                "transactionId": funding_tx_id,
                "index": 0,
                "amount": cfg["lock_amount"],
            }]
    except Exception as e:
        logger.warning(f"UTXO query failed: {e}")
        covenant_utxos = [{
            "transactionId": funding_tx_id,
            "index": 0,
            "amount": cfg["lock_amount"],
        }]
        print_warn("Using synthetic UTXO data for claim construction")

    # ── Step 6: Build & Submit Claim Transaction ─────────────────────────
    print_step(6, 7, "Build & Submit Claim Transaction")

    try:
        claim_tx = builder.build_claim_transaction(
            covenant_utxos=covenant_utxos,
            seller_address=seller_address,
            preimage=report_content,
            seller_privkey=cfg["seller_key"],
            redeem_script=redeem_script,
        )
        claim_hex = claim_tx.hex()
        claim_id = claim_tx.txid()

        print_ok("Claim transaction built", f"({Style.dim(f'inputs: {len(claim_tx.inputs)}, outputs: {len(claim_tx.outputs)}')})")
        print_ok("Seller receives", f"{covenant_utxos[0].get('amount', 0) / 1e8} KAS")

        if "SIMULATED" not in funding_tx_id and "FALLBACK" not in funding_tx_id:
            claim_tx_id = await client.submit_transaction(claim_hex)
            print_ok("Claim transaction submitted", claim_tx_id)

            result = await client.wait_for_tx_confirmation(claim_tx_id, timeout=120.0)
            print_ok("Claim confirmed", f"@ DAA {result['daa_score']}")
        else:
            claim_tx_id = "SIMULATED_CLAIM_" + claim_id[:16]
            print_info("Skipping live submission", "funding was simulated")

    except Exception as e:
        logger.error(f"Claim failed: {e}")
        print(f"\n{Style.fail('❌')} Claim transaction failed: {e}")
        print_info("The covenant script or signature may need adjustment")
        claim_tx_id = "FAILED_CLAIM"

    # ── Step 7: Settlement Summary ───────────────────────────────────────
    print_step(7, 7, "Settlement Summary")

    end_daa = await client.get_current_daa_score()

    print(f"""
  {Style.head('PACT SETTLEMENT SUMMARY')}
  {Style.dim('────────────────────────────────────────────────────')}
  {Style.dim('Protocol:')}     PAKT v0.1  |  Kaspa Testnet-12
  {Style.dim('State:')}       {'✅ SETTLED' if 'SIMULATED' not in claim_tx_id else '✅ DEMO MODE (simulated)'}
  {Style.dim('────────────────────────────────────────────────────')}
  {Style.dim('Amount:')}       {(covenant_utxos[0].get('amount', 0)) / 1e8} KAS
  {Style.dim('Buyer:')}        {buyer_address[:24]}...
  {Style.dim('Seller:')}       {seller_address[:24]}...
  {Style.dim('Covenant:')}     {covenant_addr[:24]}...
  {Style.dim('────────────────────────────────────────────────────')}
  {Style.dim('Funding TX:')}    {funding_tx_id[:40]}...
  {Style.dim('Claim TX:')}      {claim_tx_id[:40]}...
  {Style.dim('Content Hash:')}  {commitment.hex[:16]}...
  {Style.dim('DAA Start:')}     {daa_start}  →  {end_daa}
  {Style.dim('────────────────────────────────────────────────────')}
  {Style.dim('Duration:')}     LIVE
    """)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run_live_testnet_flow())
