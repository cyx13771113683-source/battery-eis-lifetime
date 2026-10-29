# Isolated HDMI-capable BE03 candidate; never save over the source project.
if {$argc!=3} {error "source.xpr battery-root new-build-directory"}
lassign $argv source root output
foreach var {source root output} {set $var [file normalize [set $var]]}
if {[file exists $output]} {error "build directory must be new"}
set f [open [file join $root build sim run.log] r];set simlog [read $f];close $f
if {![string match *PASS_BATTERY_EIS* $simlog] || [regexp {Fatal:|FAIL_} $simlog]} {error "RTL simulation gate failed"}
set f [open [file join $root build sim_lowfreq run.log] r];set lowlog [read $f];close $f
if {![string match *PASS_LOWFREQ_BUFFER* $lowlog] || [regexp {Fatal:|FAIL_} $lowlog]} {error "low-frequency simulation gate failed"}
set workspace [file normalize [file join $root ../..]]
# Import the existing BD into a fresh short-path project. Opening its old XPR
# expands generated Windows paths beyond MAX_PATH before save_project_as runs.
create_project battery_eis [file join $output project] -part xc7z020clg400-1
set_property ip_repo_paths [file join $workspace build ip] [current_project]
update_ip_catalog
add_files -norecurse [glob [file join $root rtl *.sv]]
add_files -norecurse [file join $root rtl pynq_z2_waveshare_five_electrode_bd.v]
add_files -norecurse [file join $root rtl sine14.mem]
set_property file_type {Memory Initialization Files} [get_files sine14.mem]
set source_bd $source
file mkdir [file join $output imported]
set f [open $source_bd r];set bd_text [read $f];close $f
# A copied BD retains its relative generation directory. Rebind it before Vivado
# resolves that old path outside this isolated build.
set generated [file join $output generated base]
if {[regsub {"gen_directory": "[^"]*"} $bd_text "\"gen_directory\": \"$generated\"" bd_text]!=1} {
 error "BD generation directory not uniquely found"
}
set f [open [file join $output imported base.bd] w];puts $f $bd_text;close $f
add_files -norecurse [file join $output imported base.bd]
set_property ip_cache_permissions {read} [current_project]
open_bd_design [get_files base.bd]
# Match the project's existing output-only HDMI integration. The imported BD
# still contains the unused input hierarchy, whose ports have no package pins.
set hdmi_in_cell [get_bd_cells -quiet video/hdmi_in]
if {$hdmi_in_cell ne ""} {delete_bd_objs $hdmi_in_cell}
foreach name {TMDS_in DDC} {
 set pin [get_bd_intf_pins -quiet video/$name]
 if {$pin ne ""} {delete_bd_objs $pin}
}
foreach name {hdmi_in hdmi_in_ddc} {
 set port [get_bd_intf_ports -quiet $name]
 if {$port ne ""} {delete_bd_objs $port}
}
set port [get_bd_ports -quiet hdmi_in_hpd]
if {$port ne ""} {delete_bd_objs $port}
set pin [get_bd_pins video/proc_sys_reset_pixelclk/slowest_sync_clk]
set net [get_bd_nets -quiet -of_objects $pin]
if {$net ne ""} {disconnect_bd_net $net $pin}
connect_bd_net [get_bd_pins video/hdmi_out/frontend/axi_dynclk/PXL_CLK_O] $pin
set irq_zero [get_bd_cells -quiet irq_zero]
if {$irq_zero eq ""} {set irq_zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 irq_zero]}
set_property -dict [list CONFIG.CONST_VAL {0} CONFIG.CONST_WIDTH {1}] $irq_zero
foreach name {concat_interrupts/In1 concat_interrupts/In2 concat_interrupts/In4 video/proc_sys_reset_pixelclk/aux_reset_in video/xlconcat_0/In3 video/xlconcat_0/In4} {
 set pin [get_bd_pins $name]
 set net [get_bd_nets -quiet -of_objects $pin]
 if {$net ne ""} {disconnect_bd_net $net $pin}
 connect_bd_net [get_bd_pins irq_zero/dout] $pin
}
if {[get_property PART [current_project]] ne "xc7z020clg400-1"} {error "wrong FPGA part"}
if {[get_property CONFIG.PCW_FPGA3_PERIPHERAL_FREQMHZ [get_bd_cells ps7_0]] != 100} {error "expected 100MHz FCLK3"}
update_compile_order -fileset sources_1
update_module_reference [get_ips base_waveshare_lockin_0]
# Preserve accepted GPIO ABI wiring but ensure a single 100MHz domain.
set net [get_bd_nets -of_objects [get_bd_pins waveshare_lockin/clk_100m]]
if {$net ne [get_bd_nets -of_objects [get_bd_pins ps7_0/FCLK_CLK3]]} {
 disconnect_bd_net $net [get_bd_pins waveshare_lockin/clk_100m]
 connect_bd_net [get_bd_pins ps7_0/FCLK_CLK3] [get_bd_pins waveshare_lockin/clk_100m]
}
set net [get_bd_nets -of_objects [get_bd_pins waveshare_lockin/resetn]]
if {$net ne [get_bd_nets -of_objects [get_bd_pins rst_ps7_0_fclk3/peripheral_aresetn]]} {
 disconnect_bd_net $net [get_bd_pins waveshare_lockin/resetn]
 connect_bd_net [get_bd_pins rst_ps7_0_fclk3/peripheral_aresetn] [get_bd_pins waveshare_lockin/resetn]
}
# Accepted HDMI correction: sibling MMCM outputs, not a new primary PixelClk.
if {[get_property CONFIG.kGenerateSerialClk [get_bd_cells video/hdmi_out/frontend/rgb2dvi_0]] ne "false"} {error "unexpected HDMI clock topology"}
validate_bd_design
save_bd_design
file mkdir [file join $output reports]
write_bd_tcl [file join $output reports design.tcl]
generate_target all [get_files base.bd]
set wrapper [make_wrapper -files [get_files base.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 -norecurse [file join $workspace build constraints base_trimmed.xdc]
add_files -fileset constrs_1 -norecurse [file join $workspace build constraints waveshare_five_electrode.xdc]
foreach pattern {*rgb2dvi_clocks.xdc *base_clk_wiz_10MHz_0.xdc} {
 foreach f [get_files -all -quiet $pattern] {set_property IS_ENABLED false $f}
}
set xdc [file join $workspace build constraints hdmi_output_clocks.xdc]
if {![llength [get_files -quiet *hdmi_output_clocks.xdc]]} {add_files -fileset constrs_1 -norecurse $xdc}
add_files -fileset constrs_1 -norecurse [file join $root rtl battery_io.xdc]
set_property PROCESSING_ORDER LATE [get_files battery_io.xdc]
set_property synth_checkpoint_mode None [get_files base.bd]
set_property top base_wrapper [current_fileset]
foreach run [get_runs -quiet *_synth_1] {if {[regexp {ERROR|Failed} [get_property STATUS $run]]} {reset_run $run}}
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "implementation failed"}
open_run impl_1
# BEGIN_BE03_FINALIZE
set reports [file join $output reports]
set initial_setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
if {$initial_setup ne "" && $initial_setup<0} {
 report_timing_summary -file [file join $reports timing_before_postroute.rpt]
 write_xdc -exclude_physical -force [file join $reports constraints_before_postroute.xdc]
 # One bounded post-route physical optimization; clocks and exceptions unchanged.
 phys_opt_design -directive AggressiveExplore
 write_xdc -exclude_physical -force [file join $reports constraints_after_postroute.xdc]
 write_checkpoint [file join $output battery_eis_postroute.dcp]
}
report_timing_summary -file [file join $reports timing.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_drc -file [file join $reports drc.rpt]
report_cdc -file [file join $reports cdc.rpt]
report_methodology -file [file join $reports methodology.rpt]
check_timing -verbose -file [file join $reports check_timing.rpt]
report_clocks -file [file join $reports clocks.rpt]
report_bus_skew -file [file join $reports bus_skew.rpt]
report_timing -to [get_ports {ads_reset ads_cs dac_cs spi_sclk spi_mosi}] -max_paths 10 -file [file join $reports serial_outputs.rpt]
report_timing -from [get_ports spi_miso] -max_paths 5 -file [file join $reports serial_input.rpt]
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$setup eq "" || $hold eq "" || $setup<0 || $hold<0} {error "timing failed"}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {error "DRC errors"}
file mkdir [file join $output release]
write_bitstream -force [file join $output release battery_eis.bit]
# XSA export consumes the implementation run's BIT. Match the current design.
file copy -force [file join $output release battery_eis.bit] [file join [get_property DIRECTORY [get_runs impl_1]] base_wrapper.bit]
set hwh [glob [file join $generated hw_handoff base.hwh]]
file copy $hwh [file join $output release battery_eis.hwh]
write_hw_platform -fixed -include_bit [file join $output release battery_eis.xsa]
set f [open [file join $reports build_result.txt] w]
puts $f "ABI=BE03\nsetup_ns=$setup\nhold_ns=$hold\ninitial_setup_ns=$initial_setup\nboard_validated=false\npart=[get_property PART [current_project]]"
close $f
puts "PASS_BATTERY_BUILD $output"
close_project
