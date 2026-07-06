# PAKT SilverScript Covenant Debugging
# Requires: sil-debug CLI (https://github.com/kaspanet/silverscript)
#
# Usage:
#   make debug-claim     # Test the CLAIM path
#   make debug-refund    # Test the REFUND path
#   make debug-arb       # Test the ARBITRATION path
#   make debug-all       # Run all three paths
#   make verify          # Verify covenant script syntax

SIL_DEBUG = sil-debug
SCRIPT = report_pact.ss

# Test parameters
BUYER_KEY    = 0101010101010101010101010101010101010101010101010101010101010101
SELLER_KEY   = 0202020202020202020202020202020202020202020202020202020202020202
ARB_KEY      = 0303030303030303030303030303030303030303030303030303030303030303
CONTENT_HASH = 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
TIMEOUT_DAA  = 1000
ARB_TIMEOUT  = 1050

.PHONY: debug-claim debug-refund debug-arb debug-all verify

debug-claim:
	$(SIL_DEBUG) run \
		--script $(SCRIPT) \
		--param buyer_pubkey:$(BUYER_KEY) \
		--param seller_pubkey:$(SELLER_KEY) \
		--param arb_pubkey:$(ARB_KEY) \
		--param content_hash:$(CONTENT_HASH) \
		--param timeout_daa_score:$(TIMEOUT_DAA) \
		--param arb_timeout_daa:$(ARB_TIMEOUT) \
		--path claim \
		--sig seller_sig:0202020202020202020202020202020202020202020202020202020202020202 \
		--data content:"Hello, PAKT! This is the agreed report content." \
		--output-json

debug-refund:
	$(SIL_DEBUG) run \
		--script $(SCRIPT) \
		--param buyer_pubkey:$(BUYER_KEY) \
		--param seller_pubkey:$(SELLER_KEY) \
		--param arb_pubkey:$(ARB_KEY) \
		--param content_hash:$(CONTENT_HASH) \
		--param timeout_daa_score:$(TIMEOUT_DAA) \
		--param arb_timeout_daa:$(ARB_TIMEOUT) \
		--path refund \
		--sig buyer_sig:0101010101010101010101010101010101010101010101010101010101010101 \
		--ctx daa_score:$(ARB_TIMEOUT) \
		--output-json

debug-arb:
	$(SIL_DEBUG) run \
		--script $(SCRIPT) \
		--param buyer_pubkey:$(BUYER_KEY) \
		--param seller_pubkey:$(SELLER_KEY) \
		--param arb_pubkey:$(ARB_KEY) \
		--param content_hash:$(CONTENT_HASH) \
		--param timeout_daa_score:$(TIMEOUT_DAA) \
		--param arb_timeout_daa:$(ARB_TIMEOUT) \
		--path arbitrate \
		--sig arb_sig:0303030303030303030303030303030303030303030303030303030303030303 \
		--ctx daa_score:$(ARB_TIMEOUT) \
		--output-json

debug-all: debug-claim debug-refund debug-arb

verify:
	$(SIL_DEBUG) check --script $(SCRIPT)
