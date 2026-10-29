# Serial timing assumption: ADS DOUT becomes valid <=200ns after SCLK rising;
# RTL samples 32 fabric clocks later (SPI_DIV=32). Board skew still unverified.
# Scope multicycle to the only asynchronous serial data input, not internal math.
set_input_delay -clock [get_clocks clk_fpga_3] -max 200 [get_ports spi_miso]
set_input_delay -clock [get_clocks clk_fpga_3] -min 0 [get_ports spi_miso]
set_multicycle_path 32 -setup -from [get_ports spi_miso]
set_multicycle_path 31 -hold -from [get_ports spi_miso]
# These are gated serial/control outputs, not a peripheral sampling on FCLK.
# Bound clock-reference-to-pin paths to 10ns, including launch clock latency.
# The simulation proves the protocol's
# edge separations. Routed SPI register clock skew is checked separately;
# board skew must be verified <=5ns before physical acceptance.
# MOSI->falling SCLK separation is 320ns (ADS setup/hold minimum 50ns).
# ADC CS hold is >=2us (minimum 8/7.68MHz=1.042us). All internal math remains
# ordinary single-cycle timing; no blanket false path or multicycle on outputs.
# Zero external fabric-relative delay supplies the reference association; the
# explicit propagation bound below is the applicable gated-interface constraint.
set_output_delay -clock [get_clocks clk_fpga_3] -max 0 [get_ports {ads_reset ads_pdwn ads_cs dac_cs spi_sclk spi_mosi}]
set_output_delay -clock [get_clocks clk_fpga_3] -min 0 [get_ports {ads_reset ads_pdwn ads_cs dac_cs spi_sclk spi_mosi}]
set_max_delay 10 -to [get_ports {ads_reset ads_pdwn ads_cs dac_cs spi_sclk spi_mosi}]
set_min_delay 0 -to [get_ports {ads_reset ads_pdwn ads_cs dac_cs spi_sclk spi_mosi}]
