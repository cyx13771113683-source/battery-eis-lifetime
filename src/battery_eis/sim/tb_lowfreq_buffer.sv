`timescale 1ns/1ps
// Core-only scaled-clock test. ADC is a deterministic stub, not a converter model.
// The separate tb_battery_eis retains production SPI and protection regression.
module battery_adc_engine #(
 parameter integer CLK_HZ=1000000, RESET_CYCLES=1, SLOT_CYCLES=256
)(input wire clk,resetn,run,drive,phase_high,input wire [15:0] gate_high,
 input wire ads_drdy,spi_miso,input wire [31:0] tick,phase,
 output wire ads_reset,ads_pdwn,ads_cs,dac_cs,spi_sclk,spi_mosi,ready,zero_ack,
 output reg sample_valid=0,sample_channel=0,output reg signed [23:0] sample_data=0,
 output reg [31:0] sample_tick=0,sample_phase=0,
 output wire [31:0] fault,adc_config,ofc,fsc);
 integer divider=0,number=0;
 integer period=256;
 assign ads_reset=resetn;assign ads_pdwn=1;assign ads_cs=1;assign dac_cs=1;
 assign spi_sclk=0;assign spi_mosi=0;assign ready=resetn;assign zero_ack=!run;
 assign fault=0;assign adc_config=32'hf0000130;assign ofc=0;assign fsc=32'h400000;
 always @(posedge clk) begin
  sample_valid<=0;
  if(!resetn || !run) begin divider<=0;number<=0;end
  else if(divider==period-1) begin
   divider<=0;number<=number+1;sample_valid<=1;sample_channel<=number%2;
   sample_data<=number%2?30000+number%100:5000000-number%100;
   sample_tick<=tick;sample_phase<=phase;
  end else divider<=divider+1;
 end
endmodule

module tb_lowfreq_buffer;
 reg clk=0,resetn=0;reg [31:0] control=0,payload=0;
 wire [31:0] a,b,c,d,status;
 always #5 clk=~clk;
 battery_eis_core #(.CLK_HZ(1000000),.RESET_CYCLES(1),.SLOT_CYCLES(256),.WATCHDOG_CYCLES(100000)) dut(
  .clk_100m(clk),.resetn(resetn),.phase_inc(payload),.control(control),.ads_drdy(1'b0),.spi_miso(1'b0),
  .ads_reset(),.ads_pdwn(),.ads_cs(),.dac_cs(),.spi_sclk(),.spi_mosi(),
  .left_i(a),.left_q(b),.right_i(c),.right_q(d),.status(status));
 task cmd(input [3:0] op,input [31:0] value);
  begin @(negedge clk);payload=value;#100;control[26:23]=op;control[27]=~control[27];#100;end
 endtask
 task wait_done;
  begin #100;while(!status[2])begin #10000;control[28]=~control[28];end wait(status[4]);#100;end
 endtask
 integer j,n,rawfile,statsfile;
 initial begin
  #100;resetn=1;#100;
  if(status[31:16]!=16'hbe03)$fatal(1,"FAIL_LOWFREQ_ABI");
  cmd(1,9277);cmd(2,250000);cmd(3,4000000);
  cmd(4,4029755);cmd(5,6716259);cmd(6,75497);cmd(7,65535);
  control[30]=1;control[31]=1;wait_done();
  n=dut.count;
  if(status[3] || n<15000 || n>16384)$fatal(1,"FAIL_LONG_COUNT %d %h",n,status);
  rawfile=$fopen("lowfreq_raw.csv","w");$fdisplay(rawfile,"tick,phase,code,channel");
  for(j=0;j<n;j=j+1)begin
   control[22]=0;control[13:0]=j;#100;
   if(a!=dut.raw[j][31:0] || b!=dut.raw[j][63:32] || c!=dut.raw[j][95:64] || d!=dut.raw[j][127:96])
    $fatal(1,"FAIL_RAM_ADDRESS %d",j);
   $fdisplay(rawfile,"%0d,%0d,%0d,%0d",a,b,$signed(c),d);
  end
  $fclose(rawfile);
  statsfile=$fopen("lowfreq_stats.csv","w");$fdisplay(statsfile,"index,voltage,current");
  for(j=0;j<9;j=j+1)begin
   control[22]=1;control[21:16]=j;#100;
   $fdisplay(statsfile,"%0d,%0d,%0d",j,$signed({b,a}),$signed({d,c}));
  end
  $fclose(statsfile);
  control[21:16]=12;#100;
  if(a!=16384 || b!=8590 || c!=429497 || d!=4000000)$fatal(1,"FAIL_CAPABILITIES");
  $display("PASS_LONG_CAPTURE samples=%d upper_RAM_and_all_moments_exported",n);
  control[31]=0;#1000;cmd(1,8589);control[31]=1;wait_done();
  if(!dut.local_fault[8])$fatal(1,"FAIL_FREQUENCY_GUARD");
  control[31]=0;#1000;cmd(1,9277);cmd(3,4000001);control[31]=1;wait_done();
  if(!dut.local_fault[8])$fatal(1,"FAIL_WINDOW_GUARD");
  $display("PASS_BE03_LIMITS");
  control[31]=0;#1000;cmd(3,4000000);dut.engine.period=200;control[31]=1;wait_done();
  if(!dut.local_fault[5] || dut.count!=16384 || status[1])$fatal(1,"FAIL_OVERFLOW_STOP");
  $display("PASS_RAM_OVERFLOW_STOP");
  $display("PASS_LOWFREQ_BUFFER");$finish;
 end
 initial begin #100000000;$fatal(1,"FAIL_LOWFREQ_WATCHDOG");end
endmodule
