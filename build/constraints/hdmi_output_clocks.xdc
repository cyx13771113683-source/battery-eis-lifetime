# The dynamic-clock MMCM and BUFR/BUFIO primitives derive the HDMI clocks from
# clk_fpga_0 automatically.  Do not redefine their outputs as primary clocks.
# The build script disables only the invalid rgb2dvi PixelClk->SerialClk
# constraint, because those clocks are sibling outputs rather than a cascade.
