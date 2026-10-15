.PHONY: test clean
.PHONY: test_cond_sub_q test_barrett_reduce test_cond_add_q test_mod_add test_mod_sub test_ntt_butterfly
.PHONY: test_intt_butterfly test_ntt_rom test_poly_ram test_ntt_engine test_basemul_unit test_poly_basemul
.PHONY: test_compress test_decompress test_poly_addsub test_cbd_sampler test_kyber_top test_encaps_top
.PHONY: test_keygen_top test_decaps_top test_keccak_sponge test_auto_keygen test_auto_encaps
.PHONY: test_acvp_oracle test_acvp_keygen test_acvp_encaps test_acvp_decaps test_acvp
.PHONY: waves_cond_sub_q waves_barrett_reduce waves_cond_add_q waves_mod_add waves_mod_sub waves_ntt_butterfly
.PHONY: waves_intt_butterfly waves_ntt_rom waves_poly_ram waves_ntt_engine waves_basemul_unit waves_poly_basemul
.PHONY: waves_compress waves_decompress waves_poly_addsub waves_cbd_sampler waves_kyber_top waves_encaps_top
.PHONY: waves_keygen_top waves_decaps_top waves_keccak_sponge waves_auto_keygen waves_auto_encaps
.PHONY: test_fpga_top waves_fpga_top sim_evidence

test: test_cond_sub_q test_barrett_reduce test_cond_add_q test_mod_add test_mod_sub test_ntt_butterfly \
      test_intt_butterfly test_ntt_rom test_poly_ram test_ntt_engine test_basemul_unit test_poly_basemul \
      test_compress test_decompress test_poly_addsub test_cbd_sampler test_kyber_top test_encaps_top \
      test_keygen_top test_decaps_top test_keccak_sponge test_auto_keygen test_auto_encaps \
      test_fpga_top test_acvp_oracle test_acvp_keygen test_acvp_encaps test_acvp_decaps

# ACVP compliance tests (NIST FIPS 203 test vectors)
test_acvp: test_acvp_oracle test_acvp_keygen test_acvp_encaps test_acvp_decaps

test_acvp_oracle:
	python ref/test_acvp_oracle.py

test_acvp_keygen:
	cd tb/acvp_keygen && $(MAKE)

test_acvp_encaps:
	cd tb/acvp_encaps && $(MAKE)

test_acvp_decaps:
	cd tb/acvp_decaps && $(MAKE)

test_cond_sub_q:
	cd tb/cond_sub_q && $(MAKE)

test_barrett_reduce:
	cd tb/barrett_reduce && $(MAKE)

test_cond_add_q:
	cd tb/cond_add_q && $(MAKE)

test_mod_add:
	cd tb/mod_add && $(MAKE)

test_mod_sub:
	cd tb/mod_sub && $(MAKE)

test_ntt_butterfly:
	cd tb/ntt_butterfly && $(MAKE)

test_intt_butterfly:
	cd tb/intt_butterfly && $(MAKE)

test_ntt_rom:
	cd tb/ntt_rom && $(MAKE)

test_poly_ram:
	cd tb/poly_ram && $(MAKE)

test_ntt_engine:
	cd tb/ntt_engine && $(MAKE)

test_basemul_unit:
	cd tb/basemul_unit && $(MAKE)

test_poly_basemul:
	cd tb/poly_basemul && $(MAKE)

test_compress:
	cd tb/compress && $(MAKE)

test_decompress:
	cd tb/decompress && $(MAKE)

test_poly_addsub:
	cd tb/poly_addsub && $(MAKE)

test_cbd_sampler:
	cd tb/cbd_sampler && $(MAKE)

test_kyber_top:
	cd tb/kyber_top && $(MAKE)

test_encaps_top:
	cd tb/encaps_top && $(MAKE)

test_keygen_top:
	cd tb/keygen_top && $(MAKE)

test_decaps_top:
	cd tb/decaps_top && $(MAKE)

test_keccak_sponge:
	cd tb/keccak_sponge && $(MAKE)

test_auto_keygen:
	cd tb/auto_keygen && $(MAKE)

test_auto_encaps:
	cd tb/auto_encaps && $(MAKE)

# Annotated evidence waveforms + logs → simulate_result/ (see docs/vcd_waveform_guide_zh.md)
sim_evidence:
	cd tb/evidence_ntt && $(MAKE)
	cd tb/evidence_encaps && $(MAKE)
	cd tb/evidence_uart && $(MAKE)
	@echo; grep -h "^RESULT" simulate_result/logs/*_result.txt; ls -l simulate_result/waves

# UART top-level: runs scripts/test_fpga_uart.py against the simulated FPGA
test_fpga_top:
	cd tb/fpga_top && $(MAKE)

# Waveform dumps — produces FST files viewable in GTKWave
waves_cond_sub_q:
	cd tb/cond_sub_q && $(MAKE) WAVES=1

waves_barrett_reduce:
	cd tb/barrett_reduce && $(MAKE) WAVES=1

waves_cond_add_q:
	cd tb/cond_add_q && $(MAKE) WAVES=1

waves_mod_add:
	cd tb/mod_add && $(MAKE) WAVES=1

waves_mod_sub:
	cd tb/mod_sub && $(MAKE) WAVES=1

waves_ntt_butterfly:
	cd tb/ntt_butterfly && $(MAKE) WAVES=1

waves_intt_butterfly:
	cd tb/intt_butterfly && $(MAKE) WAVES=1

waves_ntt_rom:
	cd tb/ntt_rom && $(MAKE) WAVES=1

waves_poly_ram:
	cd tb/poly_ram && $(MAKE) WAVES=1

waves_ntt_engine:
	cd tb/ntt_engine && $(MAKE) WAVES=1

waves_basemul_unit:
	cd tb/basemul_unit && $(MAKE) WAVES=1

waves_poly_basemul:
	cd tb/poly_basemul && $(MAKE) WAVES=1

waves_compress:
	cd tb/compress && $(MAKE) WAVES=1

waves_decompress:
	cd tb/decompress && $(MAKE) WAVES=1

waves_poly_addsub:
	cd tb/poly_addsub && $(MAKE) WAVES=1

waves_cbd_sampler:
	cd tb/cbd_sampler && $(MAKE) WAVES=1

waves_kyber_top:
	cd tb/kyber_top && $(MAKE) WAVES=1

waves_encaps_top:
	cd tb/encaps_top && $(MAKE) WAVES=1

waves_keygen_top:
	cd tb/keygen_top && $(MAKE) WAVES=1

waves_decaps_top:
	cd tb/decaps_top && $(MAKE) WAVES=1

waves_keccak_sponge:
	cd tb/keccak_sponge && $(MAKE) WAVES=1

waves_auto_keygen:
	cd tb/auto_keygen && $(MAKE) WAVES=1

waves_auto_encaps:
	cd tb/auto_encaps && $(MAKE) WAVES=1

waves_fpga_top:
	cd tb/fpga_top && $(MAKE) WAVES=1

clean:
	cd tb/cond_sub_q && $(MAKE) clean
	cd tb/barrett_reduce && $(MAKE) clean
	cd tb/cond_add_q && $(MAKE) clean
	cd tb/mod_add && $(MAKE) clean
	cd tb/mod_sub && $(MAKE) clean
	cd tb/ntt_butterfly && $(MAKE) clean
	cd tb/intt_butterfly && $(MAKE) clean
	cd tb/ntt_rom && $(MAKE) clean
	cd tb/poly_ram && $(MAKE) clean
	cd tb/ntt_engine && $(MAKE) clean
	cd tb/basemul_unit && $(MAKE) clean
	cd tb/poly_basemul && $(MAKE) clean
	cd tb/compress && $(MAKE) clean
	cd tb/decompress && $(MAKE) clean
	cd tb/poly_addsub && $(MAKE) clean
	cd tb/cbd_sampler && $(MAKE) clean
	cd tb/kyber_top && $(MAKE) clean
	cd tb/encaps_top && $(MAKE) clean
	cd tb/keygen_top && $(MAKE) clean
	cd tb/decaps_top && $(MAKE) clean
	cd tb/keccak_sponge && $(MAKE) clean
	cd tb/auto_keygen && $(MAKE) clean
	cd tb/auto_encaps && $(MAKE) clean
	cd tb/fpga_top && $(MAKE) clean
	cd tb/evidence_ntt && $(MAKE) clean
	cd tb/evidence_encaps && $(MAKE) clean
	cd tb/evidence_uart && $(MAKE) clean
	cd tb/acvp_keygen && $(MAKE) clean
	cd tb/acvp_encaps && $(MAKE) clean
	cd tb/acvp_decaps && $(MAKE) clean
