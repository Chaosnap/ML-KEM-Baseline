VERILOG_SOURCES     = $(PWD)/../../rtl/fpga_top.v \
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
TOPLEVEL            = fpga_top
COCOTB_TEST_MODULES = test_fpga_top

# Simulation-friendly scaling: 8 clocks per UART bit, 100k-cycle timeouts
# (CLK_FREQ/10). All three cores enabled.
COMPILE_ARGS += -Pfpga_top.CLK_FREQ=1000000 -Pfpga_top.BAUD=125000 \
                -Pfpga_top.ENABLE_KEYGEN=1 -Pfpga_top.ENABLE_ENCAPS=1 -Pfpga_top.ENABLE_DECAPS=1

include ../common.mk
