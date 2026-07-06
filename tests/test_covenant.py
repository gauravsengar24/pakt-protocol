"""Tests for the PAKT covenant engine."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.covenant import (
    CovenantParams,
    CovenantTxBuilder,
    redeem_script_htlc,
    covenant_address,
    Op,
    TAG_CLAIM,
    TAG_REFUND,
    TAG_ARB,
)
from src.hash_utils import ContentCommitment


def make_params(**kwargs) -> CovenantParams:
    defaults = dict(
        buyer_pubkey=b"\x01" * 32,
        seller_pubkey=b"\x02" * 32,
        arb_pubkey=b"\x03" * 32,
        content_hash=hashlib.sha256(b"test content").digest(),
        timeout_daa=1000,
        arb_timeout_daa=1050,
        lock_amount=10_000_000_000,
    )
    defaults.update(kwargs)
    return CovenantParams(**defaults)


class TestCovenantParams:
    def test_valid_params(self):
        p = make_params()
        p.validate()

    def test_invalid_buyer_pubkey(self):
        import pytest
        with pytest.raises(AssertionError):
            make_params(buyer_pubkey=b"\x01" * 31).validate()

    def test_invalid_content_hash(self):
        import pytest
        with pytest.raises(AssertionError):
            make_params(content_hash=b"\x01" * 31).validate()

    def test_arb_after_timeout(self):
        import pytest
        with pytest.raises(AssertionError):
            make_params(arb_timeout_daa=900).validate()

    def test_negative_amount(self):
        import pytest
        with pytest.raises(AssertionError):
            make_params(lock_amount=0).validate()


class TestRedeemScript:
    def test_redeem_script_is_bytes(self):
        p = make_params()
        script = redeem_script_htlc(p)
        assert isinstance(script, bytes)
        assert len(script) > 0

    def test_redeem_script_contains_opcodes(self):
        p = make_params()
        script = redeem_script_htlc(p)
        assert Op.OP_IF in script
        assert Op.OP_SHA256 in script
        assert Op.OP_CHECKSIG in script
        assert Op.OP_CHECKLOCKTIMEVERIFY in script

    def test_content_hash_embedded(self):
        ch = hashlib.sha256(b"unique content").digest()
        p = make_params(content_hash=ch)
        script = redeem_script_htlc(p)
        assert ch in script


class TestCovenantAddress:
    def test_address_is_string(self):
        p = make_params()
        script = redeem_script_htlc(p)
        addr = covenant_address(script)
        assert isinstance(addr, str)
        assert len(addr) > 0

    def test_address_starts_with_prefix(self):
        p = make_params()
        script = redeem_script_htlc(p)
        addr = covenant_address(script, prefix="kaspatest")
        assert addr.startswith("kaspatest:")

    def test_different_scripts_different_addresses(self):
        p1 = make_params(content_hash=hashlib.sha256(b"a").digest())
        p2 = make_params(content_hash=hashlib.sha256(b"b").digest())
        s1 = redeem_script_htlc(p1)
        s2 = redeem_script_htlc(p2)
        assert covenant_address(s1) != covenant_address(s2)


class TestCovenantTxBuilder:
    def test_builder_creation(self):
        p = make_params()
        builder = CovenantTxBuilder(p)
        assert builder.network_id == "testnet-12"

    def test_describe(self):
        p = make_params()
        builder = CovenantTxBuilder(p)
        desc = builder.describe()
        assert desc["type"] == "pakt_htlc_v1"
        assert desc["lock_amount_sompi"] == 10_000_000_000
        assert "participants" in desc

    def test_funding_tx_structure(self):
        p = make_params()
        builder = CovenantTxBuilder(p)
        tx = builder.build_funding("kaspatest:change123")
        assert tx["type"] == "funding"
        assert len(tx["outputs"]) == 2
        assert tx["outputs"][0]["amount"] == 10_000_000_000

    def test_claim_tx_structure(self):
        p = make_params()
        builder = CovenantTxBuilder(p)
        tx = builder.build_claim(
            funding_txid="abc123",
            funding_index=0,
            seller_address="kaspatest:seller",
            seller_sig=b"\x04" * 64,
            content=b"market analysis report",
        )
        assert tx["type"] == "claim"
        assert len(tx["inputs"]) == 1
        assert len(tx["outputs"]) == 1

    def test_refund_tx_structure(self):
        p = make_params()
        builder = CovenantTxBuilder(p)
        tx = builder.build_refund(
            funding_txid="abc123",
            funding_index=0,
            buyer_address="kaspatest:buyer",
            buyer_sig=b"\x05" * 64,
        )
        assert tx["type"] == "refund"
        assert len(tx["inputs"]) == 1

    def test_arbitrate_tx_structure(self):
        p = make_params()
        builder = CovenantTxBuilder(p)
        tx = builder.build_arbitrate(
            funding_txid="abc123",
            funding_index=0,
            seller_address="kaspatest:seller",
            buyer_address="kaspatest:buyer",
            arb_sig=b"\x06" * 64,
            seller_share_pct=0.6,
        )
        assert tx["type"] == "arbitrate"
        assert len(tx["outputs"]) == 2


class TestHashUtils:
    def test_content_commitment(self):
        content = b"market analysis report for Q3 2026"
        commitment = ContentCommitment(content)
        assert commitment.hex == hashlib.sha256(content).hexdigest()
        assert commitment.verify(content)

    def test_commitment_from_string(self):
        c = ContentCommitment.from_string("hello")
        assert isinstance(c.digest, bytes)
        assert len(c.digest) == 32

    def test_verify_content(self):
        from src.hash_utils import verify_content
        content = b"test data"
        h = hashlib.sha256(content).hexdigest()
        assert verify_content(content, h)
        assert not verify_content(content, h[:-1] + "0")

    def test_commitment_clone(self):
        c = ContentCommitment(b"test")
        clone = c.clone_as_commitment()
        assert clone["type"] == "sha256"
        assert clone["value"] == c.hex
