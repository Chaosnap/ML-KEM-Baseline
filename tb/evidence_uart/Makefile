VERILOG_SOURCES     = $(PWD)/evidence_uart_tb.v \
                      $(PWD)/../../rtl/fpga_top.v \
                      $(PWD)/../../rtl/uart_host.v \
                      $(PWD)/../../rtl/uart_rx.v \
                      $(PWD)/../../rtl/uart_tx.v \
                      $(PWD)/../../rtl/auto_keygen_top.v \
                      $(PWD)/../../rtl/auto_keygen_ctrl.v \
                      $(PWD)/../../rtl/auto_encaps_top.v \
                      $(PWD)/../../rtl/auto_encaps_ctrl.v \
                      $(PWD)/../../rtl/decaps_top.v \
                      $(PWD)/../../rtl/decaps_ctrl.v \
                      $(PWD)/../../rtl/kyber_top.v \
                      $(PWD)/../../rtl/poly_ram.v \
                      $(PWD)/../../rtl/ntt_engine.v \
                      $(PWD)/../../rtl/ntt_butterfly.v \
                      $(PWD)/../../rtl/intt_butterfly.v \
                      $(PWD)/../../rtl/ntt_rom.v \
                      $(PWD)/../../rtl/barrett_reduce.v \
                      $(PWD)/../../rtl/cond_sub_q.v \
                      $(PWD)/../../rtl/cond_add_q.v \
                      $(PWD)/../../rtl/mod_add.v \
                      $(PWD)/../../rtl/mod_sub.v \
                      $(PWD)/../../rtl/poly_basemul.v \
                      $(PWD)/../../rtl/basemul_unit.v \
                      $(PWD)/../../rtl/cbd_sampler.v \
                      $(PWD)/../../rtl/compress.v \
                      $(PWD)/../../rtl/decompress.v \
                      $(PWD)/../../rtl/keccak_sponge.v \
                      $(PWD)/../../rtl/keccak_round.v \
                      $(PWD)/../../rtl/keccak_rc.v
TOPLEVEL            = evidence_uart_tb
COCOTB_TEST_MODULES = test_evidence_uart


# Simulation scaling (8 clocks per UART bit) is set in evidence_uart_tb.v.

# Annotated VCD goes to simulate_result/waves/ (see tb/evidence_utils.py)
RESULT_DIR := $(abspath $(PWD)/../../simulate_result)
$(shell mkdir -p $(RESULT_DIR)/waves $(RESULT_DIR)/logs)
VCD_FILE := $(RESULT_DIR)/waves/uart_transactions.vcd
PLUSARGS += +vcd=$(VCD_FILE)

# Default goal: run the simulation, then merge the duplicate top-level
# scopes Icarus writes for multiple $dumpvars calls.
.DEFAULT_GOAL := evidence
.PHONY: evidence

include ../common.mk

evidence: $(COCOTB_RESULTS_FILE)
	python3 $(PWD)/../vcd_merge_scopes.py $(VCD_FILE)
