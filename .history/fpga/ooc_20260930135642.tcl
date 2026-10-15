# ooc.tcl —— 单独综合+实现每个核，输出公平对比用的报告
set part   xc7a100tcsg324-1
set period 10.000            ;# 按 100 MHz 约束，算 Fmax 用
set outdir [file normalize ./ooc_reports]
file mkdir $outdir

foreach top {auto_encaps_top auto_keygen_top decaps_top} {
    puts "=== $top ==="
    synth_design -top $top -part $part -mode out_of_context -flatten_hierarchy rebuilt
    create_clock -period $period -name clk [get_ports clk]
    opt_design
    place_design
    route_design
    report_utilization                         -file $outdir/${top}_util.rpt
    report_utilization -hierarchical -hierarchical_depth 2 \
                                               -file $outdir/${top}_util_hier.rpt
    report_timing_summary -max_paths 5         -file $outdir/${top}_timing.rpt
    close_design
}
puts "Done. Reports in $outdir"