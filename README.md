# PAKT Protocol — AI Agent Pact Engine on Kaspa

PAKT enables autonomous AI agents to negotiate, execute, and settle binding agreements enforced by **Kaspa L1 covenants**. Each Pact is a hash-locked, time-bound UTXO with three spending paths: **Claim**, **Refund**, and **Arbitration**.

---

## Core Innovation

### Kaspa as a Programmable Coordination Layer

PAKT is not "Kaspa as a payment rail." Kaspa is fundamental to every agent interaction:

- **UTXO as State Machine** — Each pact is a UTXO whose spending conditions encode the agreement state. The UTXO set itself is the canonical ledger of all active pacts.
- **Covenants as Enforcement** — Hash-locked covenants ensure funds are only released when the digital asset matches its committed SHA256 hash. No third-party escrow required.
- **1-Second Blocks as Coordination Ticks** — Agents react to DAA score changes and mempool events in real-time, enabling sub-minute negotiation→settlement cycles.
- **SilverScript Programmability** — The covenant's arbitration path adds a programmable dispute resolution layer, with introspection into the spending transaction.

### Lifecycle

```
DRAFT → NEGOTIATING → AGREED → FUNDING → LOCKED → DELIVERED → VERIFIED → SETTLED
                                                          ↘ DISPUTE → ARBITRATING → SETTLED
                                               LOCKED → EXPIRED → REFUNDING → REFUNDED
```

---

## Architecture

```
pakt-protocol/
├── contracts/
│   └── report_pact.ss          # SilverScript covenant with 3 spending paths
├── src/
│   ├── config.py               # Network, covenant, agent, demo config
│   ├── wallet.py               # BIP44 wallet management (Kaspa BIP44: 111111')
│   ├── client.py               # wRPC client + DAA/mempool event listeners
│   ├── covenant.py             # HTLC covenant script builder + tx builder
│   ├── hash_utils.py           # SHA256 pre-image generation for covenant locks
│   ├── pact.py                 # Pact state machine (14 states, validated transitions)
│   ├── agent.py                # Buyer, Seller, Arbiter agents + negotiation engine
│   └── demo.py                 # Rich CLI demo interface
├── tests/
│   ├── test_covenant.py        # 15+ tests covering params, scripts, txs
│   └── ...
└── requirements.txt
```

### Key Components

| Module | Responsibility |
|--------|---------------|
| `covenant.py` | Builds HTLC redeem scripts, P2SH covenant addresses, and funding/claim/refund/arbitration transaction blueprints |
| `pact.py` | 14-state validated state machine with event-driven transitions |
| `agent.py` | Buyer, Seller, and Arbiter agents with LLM-powered negotiation and decision-making |
| `client.py` | wRPC connection with DAA score polling and mempool transaction subscriptions |
| `hash_utils.py` | Content commitment (SHA256) for covenant hash-locks, with verification |

---

## Quick Start

### Prerequisites

- Python 3.10+
- Kaspa Testnet-12 wallet with testnet KAS (for on-chain covenant deployment)
- SilverScript CLI (for covenant debugging): `sil-debug`

### Install

```bash
pip install -r requirements.txt
```

### Run Demo

```bash
# Happy path: negotiate → deliver → verify → settle
python -m src.demo

# Dispute scenario: delivery fails → arbitration → split settlement
python -m src.demo --scenario dispute

# Timeout scenario: seller doesn't deliver → buyer refund
python -m src.demo --scenario timeout
```

### Run Tests

```bash
pytest tests/ -v
```

---

## Covenant Specification

The SilverScript contract at `contracts/report_pact.ss` implements a hash-locked time-bound covenant with three spending paths:

### Claim Path (Seller)
```
ScriptSig: <seller_sig> <content_bytes> TAG_CLAIM <redeem_script>
```
Conditions:
- `sha256(content) == committed_hash`
- Valid seller signature
- First output pays seller the full locked amount

### Refund Path (Buyer)
```
ScriptSig: <buyer_sig> TAG_REFUND <redeem_script>
```
Conditions:
- Current DAA score > `timeout_daa_score`
- Valid buyer signature
- Full amount returned to buyer

### Arbitration Path (Arbitrator)
```
ScriptSig: <arb_sig> TAG_ARB <redeem_script>
```
Conditions:
- Current DAA score > `arb_timeout_daa_score`
- Valid arbitrator signature
- At most 2 outputs splitting the locked amount

---

## Demo Scenarios

### Happy Path (default)
Two AI agents negotiate a market report delivery. Buyer locks 35 KAS in a covenant. Seller delivers. Buyer's AI verifies the SHA256 hash matches. Covenant releases funds.

### Dispute
Delivery content doesn't match the committed hash. Buyer raises dispute. Arbitrator agent reviews evidence and splits funds 50/50.

### Timeout
Seller never delivers. After DAA timeout, buyer reclaims full locked amount via refund path.

---

## Security

This codebase was designed with the Kaspa SDK audit findings in mind:

- **No panics/unwraps** — All error paths use proper exception handling
- **Key zeroization** — Wallet private keys can be explicitly cleared (`Wallet.zeroize()`)
- **No unsafe code** — Pure Python with audited SDK bindings
- **Input validation** — All covenant parameters validated before transaction building

---

## License

MIT
