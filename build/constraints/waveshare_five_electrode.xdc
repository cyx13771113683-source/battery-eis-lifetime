## Waveshare High-Precision AD/DA Board on PYNQ-Z2 Raspberry Pi 40-pin header.
set_property -dict {PACKAGE_PIN U7  IOSTANDARD LVCMOS33} [get_ports ads_drdy]
set_property -dict {PACKAGE_PIN C20 IOSTANDARD LVCMOS33} [get_ports ads_reset]
set_property -dict {PACKAGE_PIN V7  IOSTANDARD LVCMOS33} [get_ports ads_pdwn]
set_property -dict {PACKAGE_PIN U8  IOSTANDARD LVCMOS33} [get_ports ads_cs]
set_property -dict {PACKAGE_PIN W6  IOSTANDARD LVCMOS33} [get_ports dac_cs]
set_property -dict {PACKAGE_PIN V8  IOSTANDARD LVCMOS33} [get_ports spi_mosi]
set_property -dict {PACKAGE_PIN V10 IOSTANDARD LVCMOS33} [get_ports spi_miso]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVCMOS33} [get_ports spi_sclk]

# DRDY is asynchronous and enters a two-flop synchronizer. MISO is sampled by
# the 100 MHz SPI state machine after a full half-SCLK interval.
set_false_path -from [get_ports ads_drdy]
