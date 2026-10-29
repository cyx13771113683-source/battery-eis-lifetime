# PixelClk and SerialClk are separate outputs of the upstream dynamic-clock
# MMCM.  At the rgb2dvi OOC boundary there is no logical clock path between
# these input ports, so model both as primary clocks.
create_clock -name rgb2dvi_ooc_pixel -period 10.000 [get_ports PixelClk]
create_clock -name rgb2dvi_ooc_serial -period 2.000 [get_ports SerialClk]
