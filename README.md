---
title: PAKT Protocol
emoji: 🤝
colorFrom: indigo
colorTo: cyan
sdk: docker
pinned: false
app_port: 7860
short_description: AI Agent Pact Engine on Kaspa
---

# 🤝 PAKT Protocol — AI Agent Pact Engine on Kaspa

[![Hugging Face Spaces](https://img.shields.io/badge/🤗-Spaces-blue)](https://huggingface.co/spaces/gauravsengar24/pakt-protocol)
[![GitHub](https://img.shields.io/badge/GitHub-Repo-181717?logo=github)](https://github.com/gauravsengar24/pakt-protocol)

PAKT enables autonomous AI agents to negotiate, execute, and settle binding agreements enforced by **Kaspa L1 covenants**. Each Pact is a hash-locked, time-bound UTXO with three spending paths: **Claim**, **Refund**, and **Arbitration**.

---

## ✨ Try It

Select a scenario in the sidebar and click **Run**:

| Scenario | Description |
|----------|-------------|
| ✅ **Happy Path** | Agents negotiate → covenant locks funds → content delivered → verified → settled |
| ⚖️ **Dispute** | Content fails verification → arbitrator splits funds on-chain |
| ⏰ **Timeout** | No delivery → DAA timeout → buyer refunds full amount |

---

## 🏗 Architecture

```
pakt-protocol/
├── contracts/report_pact.ss   # SilverScript covenant (3 spending paths)
├── src/
│   ├── covenant.py            # HTLC script builder + tx builder
│   ├── pact.py                # 14-state validated state machine
│   ├── agent.py               # Buyer/Seller/Arbiter LLM agents
│   ├── kaspa_rpc.py           # Pure-Python wRPC client (no Rust)
│   ├── kaspa_tx.py            # Pure-Python tx builder + signer
│   ├── hash_utils.py          # SHA256 pre-image generation
│   ├── testnet_live_deploy.py # Live Testnet-12 deployment script
│   └── demo.py                # Terminal demo (3 scenarios)
├── app/streamlit_app.py       # HF Spaces web interface
└── Dockerfile                 # HF Spaces deployment
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **UTXO as state machine** | Each pact IS a UTXO — the covenant's spending conditions encode the agreement state |
| **Pure-Python tx builder** | Zero Rust dependency — `kaspa_tx.py` builds and signs transactions using `ecdsa` + `websockets` |
| **3-path SilverScript** | Claim (hash match), Refund (timeout), Arbitration (dispute) — no trusted third party |
| **14-state lifecycle** | Every transition validated — impossible to skip from DRAFT to SETTLED |

---

## 🚀 Live Testnet Deployment

```bash
# Export Testnet-12 private keys
export PAKT_TESTNET_BUYER_KEY=<hex>
export PAKT_TESTNET_SELLER_KEY=<hex>

# Fund at: https://faucet.testnet-12.kaspa.org

# Deploy a covenant and execute the full lifecycle
python -m src.testnet_live_deploy
```

---

## 🧪 Tests

```bash
pip install -r requirements.txt
pytest tests/ -v    # 40 tests passing
```

---

## 📄 License

MIT
