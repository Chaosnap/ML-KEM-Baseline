# build.tcl — Create, build and save the Vivado project for fpga_top
#             (ML-KEM-768 over UART) on a Digilent Arty A7-100T.
#
# Run from the Vivado Tcl Console (any working directory):
#     source /path/to/ML-KEM-Baseline/fpga/build.tcl
# or in batch mode:
#     vivado -mode batch -source fpga/build.tcl
#
# Result:
#   fpga/vivado_proj/ml_kem_uart.xpr   Vivado project (reopen with File > Open Project)
#   fpga/vivado_proj/ml_kem_uart.runs/impl_1/fpga_top.bit
#   fpga/vivado_proj/reports/          timing + utilization reports
#
# Any project already open in the GUI is closed first; an existing
# fpga/vivado_proj is overwritten.

# ─── Settings ────────────────────────────────────────────────────────
set proj_name  ml_kem_uart
set part       xc7a100tcsg324-1
set xdc_file   arty_a7.xdc
set baud       115200
set jobs       4

# Core clock: 1000 MHz VCO / MMCM_DIV. The cores are not pipelined
# (basemul_unit: 5 chained DSP48s, ~41 ns), so 20 MHz is required.
set mmcm_div   50
set clk_freq   [expr {1000000000 / $mmcm_div}]

set generics [list \
    CLK_FREQ=$clk_freq \
    MMCM_DIV=$mmcm_div \
    BAUD=$baud \
    RST_ACTIVE_LOW=1 \
    ENABLE_KEYGEN=1 \
    ENABLE_ENCAPS=1 \
    ENABLE_DECAPS=1 ]

# ─── Paths ───────────────────────────────────────────────────────────
set root     [file normalize [file join [file dirname [info script]] ..]]
set proj_dir [file join $root fpga vivado_proj]
set rpt_dir  [file join $proj_dir reports]

set rtl_files {
    fpga_top.v uart_host.v uart_rx.v uart_tx.v
    auto_keygen_top.v auto_keygen_ctrl.v auto_encaps_top.v auto_encaps_ctrl.v
    decaps_top.v decaps_ctrl.v kyber_top.v poly_ram.v
    ntt_engine.v ntt_butterfly.v intt_butterfly.v ntt_rom.v
    barrett_reduce.v cond_sub_q.v cond_add_q.v mod_add.v mod_sub.v
    poly_basemul.v basemul_unit.v cbd_sampler.v compress.v decompress.v
    keccak_sponge.v keccak_round.v keccak_rc.v
}

# ─── Create project ──────────────────────────────────────────────────
if {[llength [get_projects -quiet]] > 0} { close_project }
create_project $proj_name $proj_dir -part $part -force
set_property target_language Verilog [current_project]

set srcs {}
foreach f $rtl_files { lappend srcs [file join $root rtl $f] }
lappend srcs [file join $root rtl kyber_pkg.vh]
add_files -norecurse -fileset sources_1 $srcs
set_property file_type {Verilog Header} [get_files kyber_pkg.vh]
set_property is_global_include false    [get_files kyber_pkg.vh]

add_files -norecurse -fileset constrs_1 [file join $root fpga constraints $xdc_file]

set_property top          fpga_top                 [get_filesets sources_1]
set_property include_dirs [list [file join $root rtl]] [get_filesets sources_1]
set_property generic      $generics                [get_filesets sources_1]
update_compile_order -fileset sources_1

puts "Project: [file join $proj_dir $proj_name.xpr]"
puts "Part:    $part   Core clock: [expr {$clk_freq / 1000000.0}] MHz   Generics: $generics"

# ─── Synthesis + implementation + bitstream ──────────────────────────
reset_run synth_1
launch_runs synth_1 -jobs $jobs
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "Synthesis failed — see [get_property DIRECTORY [get_runs synth_1]]/runme.log"
}

launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "Implementation failed — see [get_property DIRECTORY [get_runs impl_1]]/runme.log"
}

# ─── Reports ─────────────────────────────────────────────────────────
open_run impl_1
file mkdir $rpt_dir
report_timing_summary -file [file join $rpt_dir timing.rpt]
report_utilization    -file [file join $rpt_dir utilization.rpt]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
set bit [file join [get_property DIRECTORY [get_runs impl_1]] fpga_top.bit]

puts ""
puts "Bitstream: $bit"
puts "Timing:    WNS = $wns ns, WHS = $whs ns"
if {$wns < 0 || $whs < 0} {
    puts "WARNING: timing NOT met — the design will not work reliably. Increase mmcm_div."
} else {
    puts "Timing met. Program with: source [file join $root fpga program.tcl]"
}
