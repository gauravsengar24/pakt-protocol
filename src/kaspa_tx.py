"""
PAKT Kaspa Transaction Builder — Pure-Python, zero Rust dependency.

Constructs, signs, and serializes Kaspa transactions for the PAKT
covenant lifecycle. Supports:

  - P2PKH address derivation (testnet + mainnet)
  - P2SH covenant address derivation
  - Funding transactions (P2PKH → P2SH)
  - Claim transactions (P2SH → P2PKH with hash pre-image)
  - Refund transactions (P2SH → P2PKH after timeout)
  - ECDSA secp256k1 signing

Usage:
    builder = KaspaTxBuilder(network="testnet-12")
    addr = builder.p2pkh_address(pubkey_bytes)
    covenant_addr = builder.p2sh_address(redeem_script_bytes)
    tx = builder.build_funding(utxos, covenant_addr, amount, change_addr, sk)
    tx_hex = tx.serialize().hex()
"""

from __future__ import annotations

import hashlib
import struct
from typing import Optional

from ecdsa import SigningKey, VerifyingKey, SECP256k1
from ecdsa.util import sigencode_der, sigdecode_der

from src.covenant import Op, CovenantParams, CovenantTxBuilder


# ── Network Constants ────────────────────────────────────────────────────────

NETWORK_PARAMS = {
    "testnet-12": {
        "prefix": "kaspatest",
        "p2pkh_version": 0x7f,
        "p2sh_version": 0xc4,
    },
    "mainnet": {
        "prefix": "kaspa",
        "p2pkh_version": 0x00,
        "p2sh_version": 0x08,
    },
}

SIGHASH_ALL = 0x01


# ── Crypto Helpers ───────────────────────────────────────────────────────────

def ripemd160(data: bytes) -> bytes:
    return hashlib.new("ripemd160", data).digest()


def hash160(data: bytes) -> bytes:
    return ripemd160(hashlib.sha256(data).digest())


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


# ── Base58 Encoding ──────────────────────────────────────────────────────────

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    chars = []
    while n > 0:
        n, r = divmod(n, 58)
        chars.append(_B58_ALPHABET[r])
    for b in data:
        if b == 0:
            chars.append(_B58_ALPHABET[0])
        else:
            break
    return "".join(reversed(chars))


def b58check_encode(payload: bytes, version: int) -> str:
    data = bytes([version]) + payload
    checksum = sha256d(data)[:4]
    return b58encode(data + checksum)


def b58check_decode(addr_str: str) -> tuple[int, bytes]:
    if ":" in addr_str:
        addr_str = addr_str.split(":", 1)[1]
    n = 0
    for c in addr_str:
        n = n * 58 + _B58_ALPHABET.index(c)
    data = n.to_bytes(25, "big")
    version = data[0]
    payload = data[1:21]
    checksum = data[21:25]
    expected = sha256d(data[:21])[:4]
    if checksum != expected:
        raise ValueError(f"Invalid address checksum for {addr_str}")
    return version, payload


# ── Script Building ──────────────────────────────────────────────────────────

def p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"".join([
        Op.OP_DUP,
        Op.OP_HASH160,
        Op.push_data(pubkey_hash),
        Op.OP_EQUALVERIFY,
        Op.OP_CHECKSIG,
    ])


def p2sh_script(script_hash: bytes) -> bytes:
    return b"".join([
        Op.OP_HASH160,
        Op.push_data(script_hash),
        Op.OP_EQUAL,
    ])


# ── Varint Encoding ──────────────────────────────────────────────────────────

def encode_varint(val: int) -> bytes:
    if val < 0xfd:
        return bytes([val])
    elif val <= 0xffff:
        return b"\xfd" + struct.pack("<H", val)
    elif val <= 0xffffffff:
        return b"\xfe" + struct.pack("<I", val)
    else:
        return b"\xff" + struct.pack("<Q", val)


# ── Key Derivation ───────────────────────────────────────────────────────────

def private_key_to_public(priv_hex: str) -> bytes:
    raw = bytes.fromhex(priv_hex)
    sk = SigningKey.from_string(raw, curve=SECP256k1)
    vk = sk.verifying_key
    return b"\x02" + vk.to_string()[:32] if vk.pubkey.point.y() % 2 == 0 else b"\x03" + vk.to_string()[:32]


def private_key_to_address(priv_hex: str, network: str = "testnet-12") -> str:
    pubkey = private_key_to_public(priv_hex)
    return public_key_to_address(pubkey, network)


def public_key_to_address(pubkey: bytes, network: str = "testnet-12") -> str:
    params = NETWORK_PARAMS.get(network, NETWORK_PARAMS["testnet-12"])
    h = hash160(pubkey)
    return f"{params['prefix']}:{b58check_encode(h, params['p2pkh_version'])}"


def script_to_address(script: bytes, network: str = "testnet-12") -> str:
    params = NETWORK_PARAMS.get(network, NETWORK_PARAMS["testnet-12"])
    h = hash160(script)
    return f"{params['prefix']}:{b58check_encode(h, params['p2sh_version'])}"


# ── Transaction Structure ────────────────────────────────────────────────────

class TxOutpoint:
    __slots__ = ("txid", "index")

    def __init__(self, txid: bytes, index: int):
        assert len(txid) == 32
        self.txid = txid
        self.index = index

    def serialize(self) -> bytes:
        return self.txid + struct.pack("<I", self.index)

    @classmethod
    def from_dict(cls, d: dict) -> "TxOutpoint":
        txid = bytes.fromhex(d["transactionId"]) if len(d["transactionId"]) == 64 else \
               bytes.fromhex(d["txid"])
        return cls(txid, d.get("index", 0))


class TxInput:
    __slots__ = ("previous_outpoint", "signature_script", "sequence")

    def __init__(self, previous_outpoint: TxOutpoint,
                 signature_script: bytes = b"",
                 sequence: int = 0xffffffff):
        self.previous_outpoint = previous_outpoint
        self.signature_script = signature_script
        self.sequence = sequence

    def serialize(self) -> bytes:
        return (self.previous_outpoint.serialize() +
                encode_varint(len(self.signature_script)) +
                self.signature_script +
                struct.pack("<I", self.sequence))


class TxOutput:
    __slots__ = ("value", "script_public_key")

    def __init__(self, value: int, script_public_key: bytes):
        self.value = value
        self.script_public_key = script_public_key

    def serialize(self) -> bytes:
        return (struct.pack("<Q", self.value) +
                encode_varint(len(self.script_public_key)) +
                self.script_public_key)


class KaspaTransaction:
    """Represents a complete Kaspa transaction."""

    def __init__(self, version: int = 0x0001):
        self.version = version
        self.inputs: list[TxInput] = []
        self.outputs: list[TxOutput] = []
        self.lock_time: int = 0
        self.subnetwork_id: bytes = b"\x00" * 20
        self.gas: int = 0
        self.payload: bytes = b""

    def serialize(self) -> bytes:
        data = struct.pack("<H", self.version)
        data += encode_varint(len(self.inputs))
        for inp in self.inputs:
            data += inp.serialize()
        data += encode_varint(len(self.outputs))
        for out in self.outputs:
            data += out.serialize()
        data += struct.pack("<Q", self.lock_time)
        data += self.subnetwork_id
        data += struct.pack("<Q", self.gas)
        data += encode_varint(len(self.payload))
        data += self.payload
        return data

    def txid(self) -> str:
        return sha256d(self.serialize())[::-1].hex()

    def hex(self) -> str:
        return self.serialize().hex()

    def __repr__(self) -> str:
        return f"KaspaTransaction(inputs={len(self.inputs)}, outputs={len(self.outputs)}, txid={self.txid()[:16]})"


# ── Signature Hash ───────────────────────────────────────────────────────────

def signature_hash(tx: KaspaTransaction, input_index: int,
                   script_pubkey: bytes) -> bytes:
    """Compute the signature hash for a transaction input (SIGHASH_ALL)."""
    inputs_serialized = b""
    for i, inp in enumerate(tx.inputs):
        if i == input_index:
            inputs_serialized += (
                inp.previous_outpoint.serialize() +
                encode_varint(len(script_pubkey)) +
                script_pubkey +
                struct.pack("<I", inp.sequence)
            )
        else:
            inputs_serialized += (
                inp.previous_outpoint.serialize() +
                b"\x00" +  # empty script
                struct.pack("<I", 0)  # zero sequence for non-signed inputs
            )

    data = struct.pack("<H", tx.version)
    data += encode_varint(len(tx.inputs))
    data += inputs_serialized
    data += encode_varint(len(tx.outputs))
    for out in tx.outputs:
        data += out.serialize()
    data += struct.pack("<Q", tx.lock_time)
    data += tx.subnetwork_id
    data += struct.pack("<Q", tx.gas)
    data += encode_varint(len(tx.payload))
    data += tx.payload
    data += struct.pack("<I", SIGHASH_ALL)

    return sha256d(data)


# ── Signing ──────────────────────────────────────────────────────────────────

def sign_input(tx: KaspaTransaction, input_index: int,
               script_pubkey: bytes, priv_hex: str) -> bytes:
    """Sign a transaction input with a private key."""
    sighash = signature_hash(tx, input_index, script_pubkey)
    raw = bytes.fromhex(priv_hex)
    sk = SigningKey.from_string(raw, curve=SECP256k1)
    sig_der = sk.sign_digest(sighash, sigencode=sigencode_der)
    return sig_der + bytes([SIGHASH_ALL])


# ── Transaction Builder ──────────────────────────────────────────────────────

class KaspaTxBuilder:
    """High-level transaction builder for the PAKT covenant lifecycle."""

    def __init__(self, network: str = "testnet-12"):
        self.network = network
        self.params = NETWORK_PARAMS.get(network, NETWORK_PARAMS["testnet-12"])

    def p2pkh_address(self, pubkey: bytes) -> str:
        return public_key_to_address(pubkey, self.network)

    def p2sh_address(self, script: bytes) -> str:
        return script_to_address(script, self.network)

    def p2pkh_script_from_address(self, address: str) -> bytes:
        version, payload = b58check_decode(address)
        return p2pkh_script(payload)

    def derive_pubkey_from_privkey(self, priv_hex: str) -> bytes:
        return private_key_to_public(priv_hex)

    def derive_p2pkh_address(self, pubkey: bytes) -> str:
        return self.p2pkh_address(pubkey)

    def derive_p2sh_address(self, buyer_pubkey: bytes, seller_pubkey: bytes,
                            report_hash: bytes, timeout_block: int) -> str:
        params = CovenantParams(
            buyer_pubkey=buyer_pubkey,
            seller_pubkey=seller_pubkey,
            arb_pubkey=b"\x00" * 32,
            content_hash=report_hash,
            timeout_daa=timeout_block,
            arb_timeout_daa=timeout_block + 50,
            lock_amount=0,
        )
        from src.covenant import redeem_script_htlc
        script = redeem_script_htlc(params)
        return self.p2sh_address(script)

    def build_funding_transaction(self, utxos: list[dict],
                                  target_p2sh: str,
                                  amount: int,
                                  buyer_privkey: str) -> KaspaTransaction:
        buyer_pubkey = self.derive_pubkey_from_privkey(buyer_privkey)
        buyer_address = self.p2pkh_address(buyer_pubkey)
        change_script = p2pkh_script(hash160(buyer_pubkey))

        target_version, target_hash = b58check_decode(target_p2sh)
        target_script = p2sh_script(target_hash)

        selected = []
        total = 0
        fee = 10_000
        for utxo in sorted(utxos, key=lambda u: u.get("amount", 0), reverse=True):
            selected.append(utxo)
            total += utxo.get("amount", 0)
            if total >= amount + fee:
                break

        if total < amount + fee:
            raise ValueError(f"Insufficient funds: have {total}, need {amount + fee}")

        tx = KaspaTransaction()
        for utxo in selected:
            tx.inputs.append(TxInput(
                TxOutpoint.from_dict(utxo),
                signature_script=b"",
                sequence=0xffffffff,
            ))

        tx.outputs.append(TxOutput(amount, target_script))
        change = total - amount - fee
        if change > 546:  # dust threshold
            tx.outputs.append(TxOutput(change, change_script))

        for i, utxo in enumerate(selected):
            script_pubkey = self._utxo_script(utxo, buyer_address)
            sig = sign_input(tx, i, script_pubkey, buyer_privkey)
            pubkey_bytes = buyer_pubkey
            tx.inputs[i].signature_script = (
                Op.push_data(sig) +
                Op.push_data(pubkey_bytes)
            )

        return tx

    def build_claim_transaction(self, covenant_utxos: list[dict],
                                seller_address: str,
                                preimage: bytes,
                                seller_privkey: str,
                                redeem_script: bytes) -> KaspaTransaction:
        seller_pubkey = self.derive_pubkey_from_privkey(seller_privkey)
        seller_script = p2pkh_script(hash160(seller_pubkey))

        utxo = covenant_utxos[0]
        amount = utxo.get("amount", 0)

        tx = KaspaTransaction()
        tx.inputs.append(TxInput(
            TxOutpoint.from_dict(utxo),
            signature_script=b"",
            sequence=0xffffffff,
        ))

        tx.outputs.append(TxOutput(amount, seller_script))

        sig = None
        sighash = signature_hash(tx, 0, redeem_script)
        raw = bytes.fromhex(seller_privkey)
        sk = SigningKey.from_string(raw, curve=SECP256k1)
        sig_der = sk.sign_digest(sighash, sigencode=sigencode_der)
        sig = sig_der + bytes([SIGHASH_ALL])

        tx.inputs[0].signature_script = (
            Op.push_data(sig) +
            Op.push_data(preimage) +
            Op.OP_TRUE +
            Op.push_data(redeem_script)
        )

        return tx

    def _utxo_script(self, utxo: dict, address: str) -> bytes:
        from src.covenant import Op
        version, payload = b58check_decode(address)
        return p2pkh_script(payload)
