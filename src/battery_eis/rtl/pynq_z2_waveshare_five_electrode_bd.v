// Matching port shell allows isolated reuse of the accepted HDMI/GPIO BD.
module pynq_z2_waveshare_five_electrode_bd #(parameter integer SINGLE_CHANNEL=0,FIXED_RETURN=0)(
 input wire clk_100m,resetn,input wire [31:0] phase_inc,control,
 input wire ads_drdy,spi_miso,
 output wire ads_reset,ads_pdwn,ads_cs,dac_cs,spi_sclk,spi_mosi,
 output wire [31:0] left_i,left_q,right_i,right_q,status
);
 battery_eis_core impl(.clk_100m(clk_100m),.resetn(resetn),.phase_inc(phase_inc),.control(control),
 .ads_drdy(ads_drdy),.spi_miso(spi_miso),.ads_reset(ads_reset),.ads_pdwn(ads_pdwn),
 .ads_cs(ads_cs),.dac_cs(dac_cs),.spi_sclk(spi_sclk),.spi_mosi(spi_mosi),
 .left_i(left_i),.left_q(left_q),.right_i(right_i),.right_q(right_q),.status(status));
endmodule
