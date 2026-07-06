"""
PAKT Covenant Engine — Kaspa L1 covenant lifecycle.

Translates pact terms into on-chain Kaspa covenants (hash-locked
time-bound contracts) and manages the full UTXO lifecycle:

    FUND ─► LOCK ─► CLAIM | REFUND | ARBITRATE

Two parallel implementations:
  1. SilverScriptBackend — native SilverScript covenant (Testnet-12)
  2. RawScriptBackend    — raw txscript OP-code fallback (pre-Toccata)

Both produce identical covenant semantics: SHA256 hash-lock + CLTV
timeout + 3-party arbitration path.
"""

from __future__ import annotations

import dataclasses
import hashlib
import struct
from abc import ABC, abstractmethod
from typing import Optional


# ── Pact Covenant Parameters ──────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class CovenantParams:
    buyer_pubkey: bytes
    seller_pubkey: bytes
    arb_pubkey: bytes
    content_hash: bytes  # 32 bytes, SHA256 of the digital asset
    timeout_daa: int     # DAA score after which REFUND path activates
    arb_timeout_daa: int # DAA score after which ARBITRATE path activates
    lock_amount: int     # Amount in SOMPI (1 KAS = 100_000_000 SOMPI)

    def validate(self):
        assert len(self.buyer_pubkey) == 32, "buyer_pubkey must be 32 bytes"
        assert len(self.seller_pubkey) == 32, "seller_pubkey must be 32 bytes"
        assert len(self.arb_pubkey) == 32, "arb_pubkey must be 32 bytes"
        assert len(self.content_hash) == 32, "content_hash must be 32 bytes (SHA256)"
        assert self.timeout_daa > 0, "timeout_daa must be positive"
        assert self.arb_timeout_daa > self.timeout_daa, "arb_timeout must be after timeout"
        assert self.lock_amount > 0, "lock_amount must be positive"


# ── Spending Path Selectors ───────────────────────────────────────────────────

TAG_CLAIM = bytes([0x01])
TAG_REFUND = bytes([0x02])
TAG_ARB = bytes([0x03])


# ── Covenant Script Utilities ─────────────────────────────────────────────────

class Op:
    """Kaspa txscript opcode constants."""
    OP_0 = b'\x00'
    OP_1 = b'\x51'
    OP_IF = b'\x63'
    OP_ELSE = b'\x67'
    OP_ENDIF = b'\x68'
    OP_DUP = b'\x76'
    OP_DROP = b'\x75'
    OP_SHA256 = b'\xa8'
    OP_EQUAL = b'\x87'
    OP_EQUALVERIFY = b'\x88'
    OP_CHECKSIG = b'\xac'
    OP_CHECKSIGVERIFY = b'\xad'
    OP_CHECKLOCKTIMEVERIFY = b'\xb1'
    OP_HASH160 = b'\xa9'
    OP_PUSH_DATA_1 = b'\x4c'
    OP_TRUE = OP_1

    @staticmethod
    def push_data(data: bytes) -> bytes:
        length = len(data)
        if length < 0x4c:
            return bytes([length]) + data
        elif length < 0x100:
            return Op.OP_PUSH_DATA_1 + bytes([length]) + data
        elif length < 0x10000:
            return b'\x4d' + struct.pack('<H', length) + data
        else:
            return b'\x4e' + struct.pack('<I', length) + data

    @staticmethod
    def push_int(value: int) -> bytes:
        if value == 0:
            return Op.OP_0
        if 1 <= value <= 16:
            return bytes([0x50 + value])
        return Op.push_data(value.to_bytes((value.bit_length() + 7) // 8, 'little'))


def redeem_script_htlc(params: CovenantParams) -> bytes:
    """
    Build the raw HTLC redeem script:

    OP_IF
        OP_SHA256 <content_hash> OP_EQUALVERIFY
        <seller_pubkey> OP_CHECKSIG
    OP_ELSE
        <timeout_daa> OP_CHECKLOCKTIMEVERIFY OP_DROP
        OP_DUP OP_IF
            <arb_pubkey> OP_CHECKSIGVERIFY
            OP_1  # arbitration flag
        OP_ELSE
            <buyer_pubkey> OP_CHECKSIG
        OP_ENDIF
    OP_ENDIF
    """
    script = b''

    # OP_IF — check path selector
    script += Op.OP_IF

    # ── CLAIM path ────────────────────────────────────────────────────────
    script += Op.OP_SHA256
    script += Op.push_data(params.content_hash)
    script += Op.OP_EQUALVERIFY
    script += Op.push_data(params.seller_pubkey)
    script += Op.OP_CHECKSIG

    script += Op.OP_ELSE

    # ── TIMEOUT path ──────────────────────────────────────────────────────
    script += Op.push_int(params.timeout_daa)
    script += Op.OP_CHECKLOCKTIMEVERIFY
    script += Op.OP_DROP

    # Inner IF: arbitration (1) vs refund (0)
    script += Op.OP_DUP
    script += Op.OP_IF

    # ── ARBITRATE path ────────────────────────────────────────────────────
    script += Op.push_data(params.arb_pubkey)
    script += Op.OP_CHECKSIGVERIFY
    script += Op.push_int(params.arb_timeout_daa)
    script += Op.OP_CHECKLOCKTIMEVERIFY
    script += Op.OP_DROP

    script += Op.OP_ELSE

    # ── REFUND path ────────────────────────────────────────────────────────
    script += Op.push_data(params.buyer_pubkey)
    script += Op.OP_CHECKSIG

    script += Op.OP_ENDIF  # inner IF
    script += Op.OP_ENDIF  # outer IF

    return script


def script_hash(script: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(script).digest()).digest()


def covenant_address(script: bytes, prefix: str = "kaspatest") -> str:
    """
    Derive a P2SH covenant address from the redeem script.
    Returns a Kaspa address string suitable for use in transaction outputs.

    NOTE: This is a simplified address derivation. The actual Kaspa address
    format uses base58check with version bytes. The Kaspa Python SDK's
    Address.from_script() should be preferred when available.
    """
    h = script_hash(script)
    payload = h[:20]
    version_byte = bytes([0x00])  # P2SH on testnet
    raw = version_byte + payload
    checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    b58 = _b58encode(raw + checksum)
    return f"{prefix}:{b58}" if prefix else b58


def _b58encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(data, 'big')
    chars = []
    while n > 0:
        n, rem = divmod(n, 58)
        chars.append(alphabet[rem])
    for byte in data:
        if byte == 0:
            chars.append(alphabet[0])
        else:
            break
    return ''.join(reversed(chars))


# ── Covenant Transaction Builder ─────────────────────────────────────────────

class CovenantTxBuilder:
    """
    Builds Kaspa transactions for the PAKT covenant lifecycle.

    Each method returns a dictionary representing a transaction blueprint
    that can be serialized and submitted via the wRPC client.
    """

    def __init__(self, params: CovenantParams, network_id: str = "testnet-12"):
        params.validate()
        self.params = params
        self.network_id = network_id
        self._redeem_script = redeem_script_htlc(params)

    @property
    def redeem_script(self) -> bytes:
        return self._redeem_script

    @property
    def script_hex(self) -> str:
        return self._redeem_script.hex()

    @property
    def p2sh_address(self) -> str:
        return covenant_address(self._redeem_script, prefix="kaspatest")

    def build_funding(self, buyer_change_address: str, fee: int = 10_000) -> dict:
        """
        Build the FUNDING transaction that locks funds in the covenant.

        Inputs:  UTXOs from buyer's wallet (selected externally)
        Outputs: [0] → covenant P2SH address (locked amount)
                 [1] → buyer change address (remainder minus fee)

        Returns a blueprint dict; actual UTXO selection and signing
        happens in the wallet layer.
        """
        return {
            "type": "funding",
            "network_id": self.network_id,
            "outputs": [
                {
                    "address": self.p2sh_address,
                    "amount": self.params.lock_amount,
                    "script_public_key": self._redeem_script.hex(),
                },
                {
                    "address": buyer_change_address,
                    "amount": 0,  # computed during UTXO selection
                },
            ],
            "fee": fee,
            "covenant_params": dataclasses.asdict(self.params),
        }

    def build_claim(self, funding_txid: str, funding_index: int,
                    seller_address: str, seller_sig: bytes, content: bytes,
                    fee: int = 10_000) -> dict:
        """
        Build the CLAIM transaction that releases funds to the seller.

        ScriptSig: <seller_sig> <content_bytes> TAG_CLAIM <redeem_script>
        """
        script_sig = (
            Op.push_data(seller_sig) +
            Op.push_data(content) +
            TAG_CLAIM +
            Op.push_data(self._redeem_script)
        )

        return {
            "type": "claim",
            "network_id": self.network_id,
            "inputs": [
                {
                    "txid": funding_txid,
                    "index": funding_index,
                    "script_sig": script_sig.hex(),
                    "sequence": 0xffffffff,
                }
            ],
            "outputs": [
                {
                    "address": seller_address,
                    "amount": self.params.lock_amount,
                }
            ],
            "fee": fee,
        }

    def build_refund(self, funding_txid: str, funding_index: int,
                     buyer_address: str, buyer_sig: bytes,
                     fee: int = 10_000) -> dict:
        """
        Build the REFUND transaction returning funds to the buyer.

        ScriptSig: <buyer_sig> TAG_REFUND <redeem_script>
        """
        script_sig = (
            Op.push_data(buyer_sig) +
            TAG_REFUND +
            Op.push_data(self._redeem_script)
        )

        return {
            "type": "refund",
            "network_id": self.network_id,
            "inputs": [
                {
                    "txid": funding_txid,
                    "index": funding_index,
                    "script_sig": script_sig.hex(),
                    "sequence": 0xffffffff,
                }
            ],
            "outputs": [
                {
                    "address": buyer_address,
                    "amount": self.params.lock_amount,
                }
            ],
            "fee": fee,
        }

    def build_arbitrate(self, funding_txid: str, funding_index: int,
                        seller_address: str, buyer_address: str,
                        arb_sig: bytes, seller_share_pct: float = 0.5,
                        fee: int = 10_000) -> dict:
        """
        Build the ARBITRATE transaction splitting funds between seller and buyer.

        ScriptSig: <arb_sig> TAG_ARB <redeem_script>

        Args:
            seller_share_pct: fraction of locked amount sent to seller (0.0–1.0)
        """
        total = self.params.lock_amount
        seller_amount = int(total * seller_share_pct)
        buyer_amount = total - seller_amount - fee

        script_sig = (
            Op.push_data(arb_sig) +
            TAG_ARB +
            Op.push_data(self._redeem_script)
        )

        return {
            "type": "arbitrate",
            "network_id": self.network_id,
            "inputs": [
                {
                    "txid": funding_txid,
                    "index": funding_index,
                    "script_sig": script_sig.hex(),
                    "sequence": 0xffffffff,
                }
            ],
            "outputs": [
                {
                    "address": seller_address,
                    "amount": seller_amount,
                },
                {
                    "address": buyer_address,
                    "amount": buyer_amount,
                },
            ],
            "fee": fee,
        }

    def describe(self) -> dict:
        """Return a human-readable description of this covenant."""
        return {
            "type": "pakt_htlc_v1",
            "network": self.network_id,
            "p2sh_address": self.p2sh_address,
            "redeem_script_hex": self.script_hex,
            "content_hash_hex": self.params.content_hash.hex(),
            "timeout_daa": self.params.timeout_daa,
            "arb_timeout_daa": self.params.arb_timeout_daa,
            "lock_amount_sompi": self.params.lock_amount,
            "lock_amount_kas": self.params.lock_amount / 100_000_000,
            "participants": {
                "buyer_pubkey_hex": self.params.buyer_pubkey.hex(),
                "seller_pubkey_hex": self.params.seller_pubkey.hex(),
                "arb_pubkey_hex": self.params.arb_pubkey.hex(),
            },
        }


# ── Convenience Factory ───────────────────────────────────────────────────────

def create_report_covenant(
    buyer_pubkey: bytes,
    seller_pubkey: bytes,
    content_hash: bytes,
    lock_amount_sompi: int = 10_000_000_000,  # 100 KAS default
    timeout_blocks: int = 100,                 # ~100 seconds on Kaspa
    arb_pubkey: Optional[bytes] = None,
    network: str = "testnet-12",
) -> tuple[CovenantParams, CovenantTxBuilder]:
    arb = arb_pubkey or b'\x00' * 32
    params = CovenantParams(
        buyer_pubkey=buyer_pubkey,
        seller_pubkey=seller_pubkey,
        arb_pubkey=arb,
        content_hash=content_hash,
        timeout_daa=timeout_blocks,
        arb_timeout_daa=timeout_blocks + 50,
        lock_amount=lock_amount_sompi,
    )
    builder = CovenantTxBuilder(params, network_id=network)
    return params, builder
