# PAKT Protocol — Demo Video Script

**Duration**: 3-4 minutes
**Format**: Terminal screencast (e.g., asciinema or QuickTime recording)
**Audio**: Voiceover explaining each phase

---

## Scene 1: Title & Context (0:00–0:30)

**Visual**: Terminal showing PAKT header art

```
╔══════════════════════════════════════════════════════════════╗
║                    PAKT PROTOCOL v0.1                        ║
║     AI Agent Pact Engine · Kaspa Covenant Layer             ║
╚══════════════════════════════════════════════════════════════╝
```

**Voiceover**:
> "PAKT enables autonomous AI agents to negotiate, execute, and settle binding agreements on Kaspa. Each pact is a hash-locked covenant UTXO with three spending paths: claim, refund, and arbitration. Kaspa isn't just a payment rail here — the UTXO itself is the state machine."

**Cut to**: Project repository in terminal
```bash
ls pakt-protocol/
# contracts/  src/  tests/  README.md
```

---

## Scene 2: The Covenant Contract (0:30–1:00)

**Visual**: Open SilverScript covenant file

```bash
cat contracts/report_pact.ss
```

**Highlight**: The 3 spending paths
- `function claim()` — seller delivers content matching SHA256 hash
- `function refund()` — buyer reclaims after DAA timeout
- `function arbitrate()` — arbitrator splits funds in dispute

**Voiceover**:
> "The covenant is written in SilverScript — Kaspa's native smart contract language. It takes five parameters: buyer key, seller key, arbitrator key, a SHA256 content hash, and timeout DAA scores. The covenant introspects the spending transaction to enforce full-amount payout and output destination."

**Optional**: Run sil-debug locally
```bash
make debug-claim
# { result: true, path: "claim", gas: 142 }
```

---

## Scene 3: Happy Path Demo (1:00–2:15)

**Visual**: Run the happy path demo

```bash
python -m src.demo --scenario happy
```

**Show each phase as it scrolls**:

> **1. NEGOTIATION** (0:15 on screen)
```
BUYER:  "I need a market analysis report"
SELLER: "Counter-offer: 35 KAS"
BUYER:  "Accepted counter-offer: 35 KAS"
SELLER: "Terms accepted. Price: 35.0 KAS"
```

**Voiceover**:
> "Two AI agents negotiate in natural language. The buyer wants a market report. They start at 50 KAS, the seller counters at 35. The buyer's LLM evaluates and accepts the deal."

> **2. COVENANT CREATION** (0:30 on screen)
```
Funding TX: sim_fund_pak... [CONFIRMED]
PACT LOCKED  |  Price: 35.00 KAS
```

**Voiceover**:
> "A covenant UTXO is created locking 35 KAS. The funds are now in a programmable UTXO — nobody controls them unilaterally."

> **3. DELIVERY & SETTLEMENT** (0:30 on screen)
```
SELLER: "Content delivered. Hash: 30963119c046..."
BUYER:  "Delivery verified. Hash matches!"
Settlement TX: sim_settle_pak... [CONFIRMED]
PAYMENT RELEASED 35.00 KAS → seller
```

**Voiceover**:
> "The seller delivers the report. The buyer's AI hashes the content and verifies it matches the covenant's commitment. Verification passes, and the covenant releases funds to the seller. Full cycle: under 60 seconds."

---

## Scene 4: Dispute Scenario (2:15–3:00)

**Visual**: Run the dispute demo

```bash
python -m src.demo --scenario dispute
```

**Show**:
```
VERIFICATION FAILED — Content hash mismatch
PACT DISPUTE
ARB: "Split: 50% seller"
Arbitration TX: sim_arb_pakt... [CONFIRMED]
SETTLED
```

**Voiceover**:
> "What if the delivered content doesn't match the committed hash? The buyer's AI detects the mismatch and raises a dispute. The covenant's third path activates: the arbitrator agent reviews the evidence and splits the locked funds. In this case, 50/50 — the seller attempted delivery but quality was insufficient. The dispute is resolved on-chain without a central authority."

---

## Scene 5: Architecture & Kaspa Integration (3:00–3:30)

**Visual**: Architecture diagram or code structure

```
pakt-protocol/
├── contracts/report_pact.ss     ← SilverScript covenant
├── src/covenant.py              ← HTLC script builder + tx builder
├── src/pact.py                  ← 14-state state machine
├── src/agent.py                 ← Buyer/Seller/Arbiter agents
├── src/client.py                ← wRPC + DAA/mempool listeners
└── src/hash_utils.py            ← SHA256 pre-image utility
```

**Voiceover**:
> "The architecture is modular. The SilverScript covenant is the on-chain anchor. The Python SDK handles transaction building and wRPC subscriptions. AI agents use LLMs for negotiation but all commitments, payments, and settlements are on Kaspa. The covenant enforces — not trust, not escrow."

---

## Scene 6: Closing (3:30–4:00)

**Visual**: Run tests

```bash
pytest tests/ -v
# 21 passed in 0.03s
```

**Voiceover**:
> "21 tests cover covenant parameter validation, script construction, all three transaction paths, and hash verification. The PAKT protocol shows what becomes possible when AI agents are powered by real-time, decentralized, programmable money on Kaspa."

**Visual**: Repository URL on screen
```
https://github.com/<your-repo>/pakt-protocol
```

---

## Production Notes

- **Screen recording**: 2560x1600 or 1920x1080, dark terminal theme
- **Font**: JetBrains Mono or Fira Code, 14pt
- **Colors**: Terminal with truecolor support (PAKT header uses ANSI 256-color)
- **Audio**: Clear voiceover, no background music (or very quiet ambient)
- **Pacing**: Allow 2-3 seconds between phase transitions for viewer to read
- **Highlight key lines** with mouse cursor or terminal selection during recording

## Alternative: Single-Take Demo (if under 3 min)

If tight on time, just run the happy path and narrate over it. Skip dispute scenario but mention it's in the repo.
