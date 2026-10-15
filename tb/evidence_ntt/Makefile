VERILOG_SOURCES     = $(PWD)/evidence_ntt_tb.v \
                      $(PWD)/../../rtl/ntt_engine.v \
                      $(PWD)/../../rtl/poly_ram.v \
                      $(PWD)/../../rtl/ntt_rom.v \
                      $(PWD)/../../rtl/ntt_butterfly.v \
                      $(PWD)/../../rtl/intt_butterfly.v \
                      $(PWD)/../../rtl/barrett_reduce.v \
                      $(PWD)/../../rtl/mod_add.v \
                      $(PWD)/../../rtl/mod_sub.v \
                      $(PWD)/../../rtl/cond_sub_q.v \
                      $(PWD)/../../rtl/cond_add_q.v
TOPLEVEL            = evidence_ntt_tb
COCOTB_TEST_MODULES = test_evidence_ntt


# Annotated VCD goes to simulate_result/waves/ (see tb/evidence_utils.py)
RESULT_DIR := $(abspath $(PWD)/../../simulate_result)
$(shell mkdir -p $(RESULT_DIR)/waves $(RESULT_DIR)/logs)
VCD_FILE := $(RESULT_DIR)/waves/ntt_roundtrip.vcd
PLUSARGS += +vcd=$(VCD_FILE)

# Default goal: run the simulation, then merge the duplicate top-level
# scopes Icarus writes for multiple $dumpvars calls.
.DEFAULT_GOAL := evidence
.PHONY: evidence

include ../common.mk

evidence: $(COCOTB_RESULTS_FILE)
	python3 $(PWD)/../vcd_merge_scopes.py $(VCD_FILE)
