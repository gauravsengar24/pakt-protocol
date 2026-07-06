// ---------------------------------------------------------------------------
// PAKT — Report Delivery Covenant (SilverScript)
// ---------------------------------------------------------------------------
// A hash-locked time-bound covenant for AI agent-to-agent digital asset
// commerce. Funds are locked in a UTXO and released when the seller
// provides content whose SHA256 matches the commitment hash.
//
// Three spending paths:
//   1. CLAIM  — seller delivers content matching committed hash
//   2. REFUND — buyer reclaims after block-height timeout
//   3. ARB    — designated arbitrator resolves disputes (garbage/empty data)
//
// Deploy target: Kaspa Testnet-12 (post-Toccata)
// Debug: sil-debug run --script contracts/report_pact.ss --args ...
// ---------------------------------------------------------------------------

// ── Covenant Parameters ──────────────────────────────────────────────────────
// Set once at covenant creation; burned into the UTXO script.

param buyer_pubkey:       PubKey    // Buyer's public key (funding party)
param seller_pubkey:     PubKey    // Seller's public key (delivery party)
param arb_pubkey:        PubKey    // Arbitrator's public key (dispute resolution)
param content_hash:      Hash256   // SHA256 of the agreed-upon digital asset
param timeout_daa_score: uint64    // DAA score after which refund is valid
param arb_timeout_daa:   uint64    // DAA score after which arbitrator can force-resolve

// ── Constants ────────────────────────────────────────────────────────────────

const TAG_CLAIM  = 0x01u8
const TAG_REFUND = 0x02u8
const TAG_ARB    = 0x03u8

// ── Spending Path: CLAIM ─────────────────────────────────────────────────────
// Seller provides the pre-image content and proves it matches the commitment.
// Condition: sha256(content) == content_hash AND valid seller signature.
//
// ScriptSig: <seller_sig> <content_bytes> TAG_CLAIM

function claim(sig seller_sig, bytes content) -> bool {
    // Verify content integrity against committed hash
    assert_eq(sha256(content), content_hash,
        "Content hash does not match covenant commitment");

    // Verify seller authorized this spend
    assert(check_sig(seller_sig, seller_pubkey),
        "Seller signature verification failed");

    // ── Covenant Introspection: enforce output goes to seller ──────────
    // Ensure the first output pays the seller (prevent theft to other addr)
    let out_script: bytes = tx.outputs[0].script_public_key;
    assert_eq(out_script, seller_pubkey.to_script(),
        "First output must pay seller's public key");

    // Ensure full locked amount is paid out (no partial spend)
    let locked_amount: uint64 = tx.value;
    assert_eq(tx.outputs[0].value, locked_amount,
        "First output must transfer full locked amount");

    return true;
}

// ── Spending Path: REFUND ────────────────────────────────────────────────────
// Buyer reclaims funds after the timeout DAA score has passed.
// Condition: current DAA score > timeout_daa_score AND valid buyer signature.
//
// ScriptSig: <buyer_sig> TAG_REFUND

function refund(sig buyer_sig) -> bool {
    // Enforce time lock
    assert(tx.daa_score > timeout_daa_score,
        "Refund timeout has not yet elapsed (current DAA < timeout)");

    // Verify buyer authorized this spend
    assert(check_sig(buyer_sig, buyer_pubkey),
        "Buyer signature verification failed");

    // Enforce full return to buyer
    let locked_amount: uint64 = tx.value;
    assert_eq(tx.outputs[0].value, locked_amount,
        "Refund must transfer full locked amount back");

    return true;
}

// ── Spending Path: ARBITRATION ───────────────────────────────────────────────
// Dispute resolution: if buyer claims delivered content is garbage/invalid,
// the arbitrator can force a split after arb_timeout_daa has elapsed.
// The arbitrator assigns a % to seller based on delivery attempt in good faith.
//
// ScriptSig: <arb_sig> TAG_ARB

function arbitrate(sig arb_sig) -> bool {
    // Enforce arbitration time lock (must be past regular timeout + buffer)
    assert(tx.daa_score > arb_timeout_daa,
        "Arbitration timeout has not yet elapsed");

    // Verify arbitrator authorized this spend
    assert(check_sig(arb_sig, arb_pubkey),
        "Arbitrator signature verification failed");

    // Covenant introspection: enforce at most 2 outputs (seller + buyer)
    let output_count: uint64 = tx.output_count;
    assert(output_count <= 2u64,
        "Arbitration split must have at most 2 outputs");

    // Ensure total output value equals locked amount
    let locked_amount: uint64 = tx.value;
    let total_out: uint64 = tx.outputs[0].value + tx.outputs[1].value;
    assert_eq(total_out, locked_amount,
        "Arbitration outputs must sum to locked amount");

    return true;
}

// ── Entry Point ──────────────────────────────────────────────────────────────
// The covenant dispatcher — determines which spending path was selected
// based on the tag byte in the ScriptSig.

fn main(bytes input_data) -> bool {
    // The last byte of the ScriptSig is the path tag
    let tag: u8 = input_data[input_data.length - 1];

    // Dispatch to the correct spending path
    if tag == TAG_CLAIM {
        // Extract: seller_sig (64 bytes), content (remaining - 1 for tag)
        let sig_bytes: bytes = input_data[0..64];
        let content: bytes = input_data[64..input_data.length - 1];
        return claim(sig_from_bytes(sig_bytes), content);
    }

    if tag == TAG_REFUND {
        // Extract: buyer_sig (64 bytes)
        let sig_bytes: bytes = input_data[0..64];
        return refund(sig_from_bytes(sig_bytes));
    }

    if tag == TAG_ARB {
        // Extract: arb_sig (64 bytes)
        let sig_bytes: bytes = input_data[0..64];
        return arbitrate(sig_from_bytes(sig_bytes));
    }

    // Unknown tag — reject
    return false;
}
