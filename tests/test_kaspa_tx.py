"""Tests for the pure-Python Kaspa transaction builder."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kaspa_tx import (
    KaspaTxBuilder, private_key_to_public, public_key_to_address,
    script_to_address, hash160, b58check_encode, b58check_decode,
    KaspaTransaction, TxInput, TxOutpoint, TxOutput,
    signature_hash, sign_input,
)
from src.covenant import CovenantParams, redeem_script_htlc


class TestKeyDerivation:
    def test_private_to_public(self):
        priv = "01" * 32
        pub = private_key_to_public(priv)
        assert len(pub) == 33  # compressed
        assert pub[0] in (b"\x02"[0], b"\x03"[0])

    def test_public_to_address(self):
        priv = "01" * 32
        pub = private_key_to_public(priv)
        addr = public_key_to_address(pub)
        assert addr.startswith("kaspatest:")
        assert len(addr) > 20

    def test_different_keys_different_addresses(self):
        pub1 = private_key_to_public("01" * 32)
        pub2 = private_key_to_public("02" * 32)
        a1 = public_key_to_address(pub1)
        a2 = public_key_to_address(pub2)
        assert a1 != a2

    def test_address_roundtrip(self):
        priv = "ab" * 32
        pub = private_key_to_public(priv)
        addr = public_key_to_address(pub)
        v, p = b58check_decode(addr)
        assert v == 0x7f  # testnet P2PKH
        assert len(p) == 20


class TestScriptToAddress:
    def test_p2sh_address(self):
        script = bytes.fromhex("a914" + "ab" * 20 + "87")
        addr = script_to_address(script)
        assert addr.startswith("kaspatest:")

    def test_covenant_address(self):
        params = CovenantParams(
            buyer_pubkey=b"\x01" * 32,
            seller_pubkey=b"\x02" * 32,
            arb_pubkey=b"\x03" * 32,
            content_hash=hashlib.sha256(b"test").digest(),
            timeout_daa=1000,
            arb_timeout_daa=1050,
            lock_amount=1_000_000_000,
        )
        script = redeem_script_htlc(params)
        addr = script_to_address(script)
        assert addr.startswith("kaspatest:")
        assert len(addr) > 20

    def test_different_params_different_addresses(self):
        p1 = CovenantParams(b"\x01" * 32, b"\x02" * 32, b"\x03" * 32,
                            hashlib.sha256(b"a").digest(), 1000, 1050, 1_000_000_000)
        p2 = CovenantParams(b"\x01" * 32, b"\x02" * 32, b"\x03" * 32,
                            hashlib.sha256(b"b").digest(), 1000, 1050, 1_000_000_000)
        a1 = script_to_address(redeem_script_htlc(p1))
        a2 = script_to_address(redeem_script_htlc(p2))
        assert a1 != a2


class TestTransaction:
    def test_empty_tx_serialization(self):
        tx = KaspaTransaction()
        data = tx.serialize()
        assert isinstance(data, bytes)
        assert len(data) > 30

    def test_tx_with_input_output(self):
        tx = KaspaTransaction()
        tx.inputs.append(TxInput(
            TxOutpoint(b"\x00" * 32, 0),
            b"", 0xffffffff
        ))
        tx.outputs.append(TxOutput(1_000_000_000, b"\x76\xa9\x14" + b"\x00" * 20 + b"\x88\xac"))
        data = tx.serialize()
        assert isinstance(data, bytes)

    def test_txid_is_string(self):
        tx = KaspaTransaction()
        txid = tx.txid()
        assert isinstance(txid, str)
        assert len(txid) == 64

    def test_tx_hex(self):
        tx = KaspaTransaction()
        h = tx.hex()
        assert isinstance(h, str)
        assert len(h) > 60


class TestTxBuilder:
    def test_builder_create(self):
        builder = KaspaTxBuilder()
        assert builder.network == "testnet-12"

    def test_builder_p2pkh_address(self):
        builder = KaspaTxBuilder()
        pub = private_key_to_public("01" * 32)
        addr = builder.derive_p2pkh_address(pub)
        assert addr.startswith("kaspatest:")

    def test_builder_derive_p2sh(self):
        builder = KaspaTxBuilder()
        buyer = private_key_to_public("01" * 32)
        seller = private_key_to_public("02" * 32)
        ch = hashlib.sha256(b"test").digest()
        addr = builder.derive_p2sh_address(buyer, seller, ch, 1000)
        assert addr.startswith("kaspatest:")

    def test_derive_pubkey(self):
        builder = KaspaTxBuilder()
        pub = builder.derive_pubkey_from_privkey("01" * 32)
        assert len(pub) == 33

    def test_funding_tx_with_mock_utxos(self):
        builder = KaspaTxBuilder()
        utxos = [{
            "transactionId": "00" * 32,
            "index": 0,
            "amount": 2_000_000_000,
        }]
        target = "kaspatest:" + b58check_encode(b"\x00" * 20, 0xc4)
        tx = builder.build_funding_transaction(utxos, target, 1_000_000_000, "01" * 32)
        assert isinstance(tx, KaspaTransaction)
        assert len(tx.inputs) == 1
        assert len(tx.outputs) == 2  # target + change

    def test_funding_tx_insufficient_funds(self):
        import pytest
        builder = KaspaTxBuilder()
        utxos = [{"transactionId": "00" * 32, "index": 0, "amount": 100}]
        target = "kaspatest:" + b58check_encode(b"\x00" * 20, 0xc4)
        with pytest.raises(ValueError, match="Insufficient funds"):
            builder.build_funding_transaction(utxos, target, 1_000_000_000, "01" * 32)

    def test_claim_tx_structure(self):
        builder = KaspaTxBuilder()
        covenant_utxos = [{"transactionId": "ff" * 32, "index": 0, "amount": 1_000_000_000}]
        seller_addr = public_key_to_address(private_key_to_public("02" * 32))
        preimage = b"test content"
        redeem_script = redeem_script_htlc(CovenantParams(
            b"\x01" * 32, b"\x02" * 32, b"\x03" * 32,
            hashlib.sha256(preimage).digest(), 1000, 1050, 1_000_000_000,
        ))
        tx = builder.build_claim_transaction(
            covenant_utxos, seller_addr, preimage, "02" * 32, redeem_script
        )
        assert isinstance(tx, KaspaTransaction)
        assert len(tx.inputs) == 1
        assert len(tx.outputs) == 1
        assert tx.outputs[0].value == 1_000_000_000

    def test_signature_hash(self):
        tx = KaspaTransaction()
        tx.inputs.append(TxInput(TxOutpoint(b"\x00" * 32, 0), b"", 0xffffffff))
        tx.outputs.append(TxOutput(1_000_000_000, b"\x76\xa9\x14" + b"\x00" * 20 + b"\x88\xac"))
        sighash = signature_hash(tx, 0, b"\x76\xa9\x14" + b"\x00" * 20 + b"\x88\xac")
        assert len(sighash) == 32
