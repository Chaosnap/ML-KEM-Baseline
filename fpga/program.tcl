# program.tcl — Program the Arty A7-100T over JTAG with the built bitstream
#
# Run from the Vivado Tcl Console after build.tcl:
#     source /path/to/ML-KEM-Baseline/fpga/program.tcl
#
# To program a different file, set it first:  set bit_file /path/to/file.bit

set root [file normalize [file join [file dirname [info script]] ..]]
if {![info exists bit_file]} {
    set bit_file [file join $root fpga vivado_proj ml_kem_uart.runs impl_1 fpga_top.bit]
}
if {![file exists $bit_file]} { error "Bitstream not found: $bit_file (run build.tcl first)" }

# Safe to re-run: reuse an already-open hardware manager / server / target
if {[catch {current_hw_server}]} {
    open_hw_manager
    connect_hw_server -allow_non_jtag
}
if {[catch {current_hw_target}] || [get_property IS_OPENED [current_hw_target]] == 0} {
    open_hw_target
}
set dev [lindex [get_hw_devices xc7a100t*] 0]
if {$dev eq ""} { error "No XC7A100T found on the JTAG chain" }
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
set_property PROGRAM.FILE $bit_file $dev
program_hw_devices $dev
puts "Programmed $dev with $bit_file"
