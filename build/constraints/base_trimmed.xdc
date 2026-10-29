# Copyright (C) 2022 Xilinx, Inc
# SPDX-License-Identifier: BSD-3-Clause

## Switches
set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVCMOS33} [get_ports {sws_2bits_tri_i[0]}]
set_property -dict {PACKAGE_PIN M19 IOSTANDARD LVCMOS33} [get_ports {sws_2bits_tri_i[1]}]

## Audio
set_property -dict {PACKAGE_PIN U9 IOSTANDARD LVCMOS33} [get_ports IIC_1_scl_io]
set_property PULLUP true [get_ports IIC_1_scl_io];
set_property -dict {PACKAGE_PIN T9 IOSTANDARD LVCMOS33} [get_ports IIC_1_sda_io]
set_property PULLUP true [get_ports IIC_1_sda_io];
set_property -dict { PACKAGE_PIN U5   IOSTANDARD LVCMOS33 } [get_ports audio_clk_10MHz]; 
set_property -dict { PACKAGE_PIN R18   IOSTANDARD LVCMOS33 } [get_ports bclk]; 
set_property -dict { PACKAGE_PIN T17   IOSTANDARD LVCMOS33 } [get_ports lrclk]; 
set_property -dict { PACKAGE_PIN G18   IOSTANDARD LVCMOS33 } [get_ports sdata_o];
set_property -dict { PACKAGE_PIN F17   IOSTANDARD LVCMOS33 } [get_ports sdata_i]; 
set_property -dict { PACKAGE_PIN M17   IOSTANDARD LVCMOS33 } [get_ports {codec_addr[0]}]
set_property -dict { PACKAGE_PIN M18   IOSTANDARD LVCMOS33 } [get_ports {codec_addr[1]}]

## Buttons
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports {btns_4bits_tri_i[0]}]
set_property -dict {PACKAGE_PIN D20 IOSTANDARD LVCMOS33} [get_ports {btns_4bits_tri_i[1]}]
set_property -dict {PACKAGE_PIN L20 IOSTANDARD LVCMOS33} [get_ports {btns_4bits_tri_i[2]}]
set_property -dict {PACKAGE_PIN L19 IOSTANDARD LVCMOS33} [get_ports {btns_4bits_tri_i[3]}]

## LEDs
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {leds_4bits_tri_o[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {leds_4bits_tri_o[1]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {leds_4bits_tri_o[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {leds_4bits_tri_o[3]}]

## RGBLEDs
set_property -dict { PACKAGE_PIN L15   IOSTANDARD LVCMOS33 } [get_ports { rgbleds_6bits_tri_o[0] }]; 
set_property -dict { PACKAGE_PIN G17   IOSTANDARD LVCMOS33 } [get_ports { rgbleds_6bits_tri_o[1] }]; 
set_property -dict { PACKAGE_PIN N15   IOSTANDARD LVCMOS33 } [get_ports { rgbleds_6bits_tri_o[2] }]; 
set_property -dict { PACKAGE_PIN G14   IOSTANDARD LVCMOS33 } [get_ports { rgbleds_6bits_tri_o[3] }];
set_property -dict { PACKAGE_PIN L14   IOSTANDARD LVCMOS33 } [get_ports { rgbleds_6bits_tri_o[4] }]; 
set_property -dict { PACKAGE_PIN M15   IOSTANDARD LVCMOS33 } [get_ports { rgbleds_6bits_tri_o[5] }]; 

## Pmoda

## Pmodb

## Arduino GPIO

## Arduino direct I2C

## Arduino direct SPI

## Arduino analog channels

## HDMI TX
set_property -dict {PACKAGE_PIN L17 IOSTANDARD TMDS_33} [get_ports hdmi_out_clk_n]
set_property -dict {PACKAGE_PIN L16 IOSTANDARD TMDS_33} [get_ports hdmi_out_clk_p]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD TMDS_33} [get_ports {hdmi_out_data_n[0]}]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD TMDS_33} [get_ports {hdmi_out_data_p[0]}]
set_property -dict {PACKAGE_PIN J19 IOSTANDARD TMDS_33} [get_ports {hdmi_out_data_n[1]}]
set_property -dict {PACKAGE_PIN K19 IOSTANDARD TMDS_33} [get_ports {hdmi_out_data_p[1]}]
set_property -dict {PACKAGE_PIN H18 IOSTANDARD TMDS_33} [get_ports {hdmi_out_data_n[2]}]
set_property -dict {PACKAGE_PIN J18 IOSTANDARD TMDS_33} [get_ports {hdmi_out_data_p[2]}]
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports {hdmi_out_hpd[0]}]

## Raspberry PI 
##  RPI_IDE Pin#   |   RP Connector  | Schematic Name | Dual Functionality
##        1        |      3v3        |     NA
##        3        |      GPIO2      |     JA4_P      |    I2C1_SDA
##        5        |      GPIO3      |     JA4_N      |    I2C1_SCL
##        7        |      GPIO4      |     JA1_P      |    GCLK0
##        9        |      GROUND     |     NA
##        11       |      GPIO17     |     RP_IO17_R
##        13       |      GPIO27     |     RP_IO27_R
##        15       |      GPIO22     |     RP_IO22_R
##        17       |      3v3        |     NA
##        19       |      GPIO10     |     RP_IO10_R   |    SPIO0_MOSI
##        21       |      GPIO9      |     RP_IO09_R   |    SPIO0_MISO
##        23       |      GPIO11     |     RP_IO11_R   |    SPIO0_SCLK
##        25       |      GROUND     |     NA
##        27       |      GPIO0      |     JA2_P       |    I2C0_SDA ID EEPROM
##        29       |      GPIO5      |     JA1_N       |    GCLK1
##        31       |      GPIO6      |     JA3_P       |    GCLK2
##        33       |      GPIO13     |     RP_IO13_R   |    PWM1
##        35       |      GPIO19     |     RP_IO19_R   |    SPIO1_MISO
##        37       |      GPIO26     |     RP_IO26_R
##        39       |      GROUND     |     NA

##        2        |      5V         |     NA
##        4        |      5V         |     NA
##        6        |      GROUND     |     NA
##        8        |      GPIO14     |     RP_IO14_R   |    UART0_TXD
##        10       |      GPIO15     |     RP_IO15_R   |    UART0_RXD
##        12       |      GPIO18     |     RP_IO18_R   |    PCM_CLK
##        14       |      GROUND     |     NA
##        16       |      GPIO23     |     RP_IO23_R
##        18       |      GPIO24     |     RP_IO24_R 
##        20       |      GROUND     |     NA
##        22       |      GPIO25     |     RP_IO25_R
##        24       |      GPIO8      |     RP_IO08_R   |    SPIO0_CE0_N
##        26       |      GPIO7      |     JA3_N       |    SPIO0_CE1_N
##        28       |      GPIO1      |     JA2_N       |    I2C0_SDC ID EEPROM
##        30       |      GROUND     |     NA
##        32       |      GPIO12     |     RP_IO12_R   |    PWM0
##        34       |      GROUND     |     NA
##        36       |      GPIO16     |     RP_IO16_R   |    SPIO1_CE2_N
##        38       |      GPIO20     |     RP_IO20_R   |    SPIO1_MOSI
##        40       |      GPIO21     |     RP_IO21_R   |    SPIO1_SCLK
