`timescale 1ns/1ps
// Digital ADC model only; no analog accuracy or board validation claim.
module tb_battery_eis;
 reg clk=0,resetn=0,drdy=1,miso=0;
 reg [31:0] payload=0,control=0;
 wire ads_reset,ads_pdwn,ads_cs,dac_cs,sclk,mosi;
 wire [31:0] a,b,c,d,status;
 always #5 clk=~clk;
 battery_eis_core #(.RESET_CYCLES(1000),.WATCHDOG_CYCLES(500000)) dut(
 .clk_100m(clk),.resetn(resetn),.phase_inc(payload),.control(control),
 .ads_drdy(drdy),.spi_miso(miso),.ads_reset(ads_reset),.ads_pdwn(ads_pdwn),
 .ads_cs(ads_cs),.dac_cs(dac_cs),.spi_sclk(sclk),.spi_mosi(mosi),
 .left_i(a),.left_q(b),.right_i(c),.right_q(d),.status(status));
 reg [7:0] registers[0:10];
 integer bits=0, mode=0, regaddr=0,remaining=0,readbit=23;
 reg [7:0] byte_in=0;
 reg [23:0] data_out=0;
 reg sync_pending=0,convert_toggle=0,convert_seen=0,cal_toggle=0,cal_seen=0;
 integer conversion_timer=0;
 reg missing_drdy=0,undervoltage=0,overvoltage=0,overcurrent=0,reverse_current=0,saturated=0,bad_id=0;
 integer adc_frames=0,dac_frames=0,dac_bits=0,positive_frames=0;
 reg [23:0] dac_frame=0;
 reg [15:0] dac_code=0;
 realtime last_ads_fall=-1e9,last_dac_fall=-1e9,last_dac_release=-1e9;
 realtime last_mosi_sample=-1e9;
 always @(negedge ads_reset) begin
  drdy=1;registers[0]=8'h30;registers[1]=1;registers[2]=0;registers[3]=8'hf0;
  registers[4]=0;registers[5]=0;registers[6]=0;registers[7]=0;
  registers[8]=0;registers[9]=0;registers[10]=8'h40;
 end
 always @(posedge ads_reset) if(resetn) conversion_timer=1000;
 always @(posedge clk) begin
  if(convert_seen!=convert_toggle) begin convert_seen=convert_toggle;conversion_timer=21000;drdy=1;end
  if(cal_seen!=cal_toggle) begin cal_seen=cal_toggle;conversion_timer=10000;drdy=1;end
  if(conversion_timer>0) begin
   conversion_timer=conversion_timer-1;
   if(conversion_timer==0 && !missing_drdy)drdy=0;
  end
  if(!ads_cs && !dac_cs)$fatal(1,"FAIL_CS_COLLISION");
 end
 always @(negedge ads_cs) begin bits=0;mode=0;readbit=23;adc_frames=adc_frames+1;end
 always @(posedge ads_cs) if(resetn && adc_frames>0 && $realtime-last_ads_fall<1042)
  $fatal(1,"FAIL_ADC_CS_HOLD");
 always @(posedge sclk) if(!ads_cs) begin
  if(mode==5) miso=data_out[readbit];
  else if(mode==4) miso=registers[regaddr][7-bits];
  else miso=0;
 end
 always @(negedge sclk) begin
  if(!ads_cs) begin
   last_ads_fall=$realtime;last_mosi_sample=$realtime;
   byte_in={byte_in[6:0],mosi};
   if(mode==5) readbit=readbit-1;
   if(bits==7) begin
    bits=0;
    case(mode)
     0:begin
      if(byte_in==8'h50 || byte_in==8'h51)begin regaddr=byte_in[3:0];mode=1;end
      else if(byte_in==8'h10)begin regaddr=0;mode=3;end
      else if(byte_in==8'hf0)cal_toggle=~cal_toggle;
      else if(byte_in==8'hfc)begin sync_pending=1;drdy=1;end
      else if(byte_in==0 && sync_pending)begin convert_toggle=~convert_toggle;sync_pending=0;end
      else if(byte_in==1)begin
       mode=5;readbit=23;
       if(registers[1]==1)data_out=saturated?24'h7fffff:(overvoltage?24'd7000000:(undervoltage?24'd1000000:24'd4970031-(dac_code>0?24'd5000:0)));
       else if(registers[1]==8'h23)data_out=reverse_current?-24'sd300:(overcurrent?24'd100000:(dac_code>0?24'd61158:0));
       else $fatal(1,"FAIL_MUX %h",registers[1]);
      end
     end
     1:begin remaining=byte_in+1;mode=2;end
     2:begin
      registers[regaddr]=regaddr==0?(byte_in|(bad_id?8'h20:8'h30)):byte_in;
      regaddr=regaddr+1;remaining=remaining-1;if(remaining==0)mode=0;
     end
     3:begin remaining=byte_in+1;mode=4;end
     4:begin regaddr=regaddr+1;remaining=remaining-1;if(remaining==0)mode=0;end
     5:begin end
    endcase
   end else bits=bits+1;
  end
  if(!dac_cs)begin dac_frame={dac_frame[22:0],mosi};dac_bits=dac_bits+1;last_dac_fall=$realtime;end
 end
 always @(mosi) if(resetn && $realtime-last_mosi_sample<50)$fatal(1,"FAIL_MOSI_HOLD");
 always @(negedge dac_cs)begin
  if(resetn && $realtime-last_dac_release<50)$fatal(1,"FAIL_DAC_CS_HIGH");
  if(resetn && $realtime-last_dac_fall<100)$fatal(1,"FAIL_DAC_RECOVERY");
  dac_bits=0;dac_frame=0;
 end
 always @(posedge dac_cs) if(resetn && dac_bits>0)begin
  if(dac_bits!=24 || dac_frame[23:16]!=8'h10)$fatal(1,"FAIL_DAC_FRAME %h bits %d",dac_frame,dac_bits);
  dac_code=dac_frame[15:0];dac_frames=dac_frames+1;
  if(dac_code!=0)positive_frames=positive_frames+1;
  last_dac_release=$realtime;
 end
 task cmd(input [3:0] op,input [31:0] value);
  begin
   @(negedge clk);payload=value;#100;
   control[26:23]=op;control[27]=~control[27];#100;
   if(status[7]!=control[27])$fatal(1,"FAIL_CMD_ACK");
  end
 endtask
 task configure;
  begin
   cmd(1,3436);cmd(2,2000000);cmd(3,20000000);
   // Final assembly: 3V minimum, 5V maximum, 0.45A positive limit.
   cmd(4,4029755);cmd(5,6716259);cmd(6,75497);cmd(7,65535);
  end
 endtask
 task await_done;
  begin
   #100;
   while(!status[2])begin #1000000;control[28]=~control[28];end
   wait(status[4]);#1000;
   if(dac_code!=0)$fatal(1,"FAIL_NOT_ZERO");
  end
 endtask
 integer file_raw,file_stats,n,j;
 initial begin
  #100;resetn=1;
  wait(status[0] && status[4]);#100;
  if(status[31:16]!=16'hbe03)$fatal(1,"FAIL_ABI");
  configure();control[30]=1;control[31]=1;
  await_done();
  if(status[3] || dut.count<550 || positive_frames<10)$fatal(1,"FAIL_NORMAL status %h count %d",status,dut.count);
  file_raw=$fopen("rtl_raw.csv","w");$fdisplay(file_raw,"tick,phase,code,channel");
  n=dut.count;
  for(j=0;j<n;j=j+1)begin
   control[22]=0;control[13:0]=j;#100;
   $fdisplay(file_raw,"%0d,%0d,%0d,%0d",a,b,$signed(c),d);
  end
  $fclose(file_raw);
  file_stats=$fopen("rtl_stats.csv","w");$fdisplay(file_stats,"index,voltage,current");
  for(j=0;j<9;j=j+1)begin
   control[22]=1;control[21:16]=j;#100;
   $fdisplay(file_stats,"%0d,%0d,%0d",j,$signed({b,a}),$signed({d,c}));
  end
  $fclose(file_stats);$display("PASS_NORMAL samples=%0d adc_frames=%0d dac_frames=%0d",n,adc_frames,dac_frames);
  control[31]=0;#1000;
  control[31]=1;#123456;control[31]=0;await_done();
  if(!dut.local_fault[9])$fatal(1,"FAIL_CANCEL");
  $display("PASS_CANCEL");
  #1000;control[31]=1; // deliberately no heartbeat
  #100;wait(status[2]);wait(status[4]);
  if(!dut.local_fault[3] || dac_code!=0)$fatal(1,"FAIL_WATCHDOG");
  $display("PASS_WATCHDOG");
  control[31]=0;#1000;undervoltage=1;control[31]=1;await_done();
  if(!dut.local_fault[6])$fatal(1,"FAIL_UNDERVOLTAGE");
  $display("PASS_UNDERVOLTAGE");
  control[31]=0;undervoltage=0;overvoltage=1;#1000;control[31]=1;await_done();
  if(!dut.local_fault[6])$fatal(1,"FAIL_OVERVOLTAGE");
  $display("PASS_OVERVOLTAGE");
  control[31]=0;overvoltage=0;overcurrent=1;#1000;control[31]=1;await_done();
  if(!dut.local_fault[7])$fatal(1,"FAIL_OVERCURRENT");
  $display("PASS_OVERCURRENT");
  control[31]=0;overcurrent=0;reverse_current=1;#1000;control[31]=1;await_done();
  if(!dut.local_fault[7])$fatal(1,"FAIL_REVERSE_CURRENT");
  $display("PASS_REVERSE_CURRENT");
  control[31]=0;reverse_current=0;saturated=1;#1000;control[31]=1;await_done();
  if(!dut.local_fault[4])$fatal(1,"FAIL_SATURATION");
  $display("PASS_SATURATION");
  control[31]=0;saturated=0;#1000;missing_drdy=1;control[31]=1;
  await_done();
  if(dac_code!=0 || !dut.engine_fault[0])$fatal(1,"FAIL_MISSING_DRDY_STOP");
  $display("PASS_ADC_TIMEOUT");
  control[31]=0;missing_drdy=0;bad_id=1;control[29]=1;#1000;control[29]=0;
  #1000;wait(status[3]);wait(status[4]);
  if(!dut.engine_fault[1] || status[0] || dac_code!=0)$fatal(1,"FAIL_ADC_ID_REJECT");
  $display("PASS_ADC_ID_REJECT");
  $display("PASS_BATTERY_EIS");$finish;
 end
 initial begin #1500000000;$fatal(1,"FAIL_TEST_WATCHDOG");end
endmodule
