`timescale 1ns/1ps
// Pin-only timing observer for the unchanged BE03 RTL and digital ADC model.
// TI ADS1256 Rev K, timing tables pp. 6-7, CLKIN = 7.68 MHz.
// TI DAC8552 Rev A, serial interface timing. No analog validation claim.
module tb_spi_contract;
 tb_battery_eis bench();
 localparam real TCLK=1000.0/7.68;
 localparam real UPDATE_DEADLINE=1000000000.0/30000.0-16.0*TCLK;
 integer bits=0,mode=0,remaining=0,data_bits=0;
 integer read_count=0,regread_count=0,sync_count=0,write_count=0,clock_count=0;
 integer reset_count=0,dac_count=0,hold_count=0,outfile;
 reg [7:0] byte_in=0;
 reg first_data_edge=0;
 realtime last_rise=-1e9,last_fall=-1e9,last_mosi=-1e9;
 realtime last_ads_fall=-1e9,last_dac_fall=-1e9,last_dac_release=-1e9;
 realtime command_ready=0,data_ready=0,data_command_end=0,last_drdy=-1e9;
 realtime reset_start=-1,read_drdy=0,read_margin=0,observed=0;
 realtime min_period=1e9,min_high=1e9,min_low=1e9,min_setup=1e9;
 realtime min_data_delay=1e9,min_cs_hold=1e9,min_read_margin=1e9;
 realtime min_sync_delay=1e9,min_write_delay=1e9,command_end=0;
 integer pending_gap=0;

 // Verification-only fault injection proves the observer catches a too-short
 // RDATA wait. Production sources/bitstream are never patched for this test.
 initial if($test$plusargs("INJECT_SHORT_RDATA_WAIT"))begin
  wait(bench.dut.engine.state==27);
  force bench.dut.engine.timer=700;
  @(posedge bench.sclk);
  release bench.dut.engine.timer;
 end

 always @(negedge bench.drdy) last_drdy=$realtime;
 always @(bench.mosi) last_mosi=$realtime;
 always @(negedge bench.ads_reset) begin
  reset_start=$realtime;command_ready=0;pending_gap=0;
 end
 always @(posedge bench.ads_reset) if(bench.resetn && reset_start>=0)begin
  if($realtime-reset_start < 4.0*TCLK) $fatal(1,"FAIL_CONTRACT_RESET");
  reset_count=reset_count+1;
 end
 always @(negedge bench.ads_cs) begin
  bits=0;mode=0;data_bits=0;first_data_edge=0;
 end
 always @(posedge bench.ads_cs) if(bench.resetn && last_ads_fall>0) begin
  observed=$realtime-last_ads_fall;
  if(observed<8.0*TCLK) $fatal(1,"FAIL_CONTRACT_ADC_CS_HOLD ns=%0.3f",observed);
  if(observed<min_cs_hold)min_cs_hold=observed;
  hold_count=hold_count+1;
 end
 always @(negedge bench.dac_cs) if(bench.resetn) begin
  if($realtime-last_dac_release<50) $fatal(1,"FAIL_CONTRACT_DAC_CS_HIGH");
  if($realtime-last_dac_fall<100) $fatal(1,"FAIL_CONTRACT_DAC_RECOVERY");
  dac_count=dac_count+1;
 end
 always @(posedge bench.dac_cs) last_dac_release=$realtime;

 always @(posedge bench.sclk) if(bench.resetn && (!bench.ads_cs || !bench.dac_cs))begin
  observed=$realtime-last_rise;
  if(observed<4.0*TCLK) $fatal(1,"FAIL_CONTRACT_SCLK_PERIOD");
  if(observed<min_period)min_period=observed;
  observed=$realtime-last_fall;
  if(observed<200) $fatal(1,"FAIL_CONTRACT_SCLK_LOW");
  if(observed<min_low)min_low=observed;
  last_rise=$realtime;
  clock_count=clock_count+1;
  if(!bench.ads_cs)begin
   if(bits==0 && mode==0)begin
    if($realtime<command_ready) $fatal(1,"FAIL_CONTRACT_COMMAND_GAP mode=%0d",pending_gap);
    observed=$realtime-command_end;
    if(pending_gap==1)begin
     if(observed<min_write_delay)min_write_delay=observed;
     write_count=write_count+1;
    end
    if(pending_gap==2)begin
     if(observed<min_sync_delay)min_sync_delay=observed;
     sync_count=sync_count+1;
    end
    pending_gap=0;
   end
   if(first_data_edge)begin
    if($realtime<data_ready) $fatal(1,"FAIL_CONTRACT_T6");
    observed=$realtime-data_command_end;
    if(observed<min_data_delay)min_data_delay=observed;
    first_data_edge=0;
   end
  end
 end
 always @(negedge bench.sclk) if(bench.resetn && (!bench.ads_cs || !bench.dac_cs))begin
  observed=$realtime-last_rise;
  if(observed<200) $fatal(1,"FAIL_CONTRACT_SCLK_HIGH");
  if(observed<min_high)min_high=observed;
  observed=$realtime-last_mosi;
  if(observed<50) $fatal(1,"FAIL_CONTRACT_DIN_SETUP");
  if(observed<min_setup)min_setup=observed;
  last_fall=$realtime;
  if(!bench.dac_cs)last_dac_fall=$realtime;
  if(!bench.ads_cs)begin
   last_ads_fall=$realtime;
   byte_in={byte_in[6:0],bench.mosi};
   if(mode==5)begin
    data_bits=data_bits+1;
    if(data_bits==24)begin
     read_margin=read_drdy+UPDATE_DEADLINE-$realtime;
     if(read_margin<0) $fatal(1,"FAIL_CONTRACT_RDATA_OVERWRITE margin_ns=%0.3f",read_margin);
     if(read_margin<min_read_margin)min_read_margin=read_margin;
     read_count=read_count+1;
    end
   end
   if(bits==7)begin
    bits=0;
    case(mode)
     0:begin
      if((byte_in & 8'hf0)==8'h50)mode=1;
      else if((byte_in & 8'hf0)==8'h10)mode=3;
      else if(byte_in==8'hfc)begin
       command_end=$realtime;command_ready=$realtime+24.0*TCLK;pending_gap=2;
      end
      else if(byte_in==8'h01)begin
       mode=5;data_bits=0;read_drdy=last_drdy;
       data_command_end=$realtime;data_ready=$realtime+50.0*TCLK;first_data_edge=1;
      end
     end
     1:begin remaining=byte_in+1;mode=2;end
     2:begin
      remaining=remaining-1;
      if(remaining==0)begin
       mode=0;command_end=$realtime;command_ready=$realtime+4.0*TCLK;pending_gap=1;
      end
     end
     3:begin
      remaining=byte_in+1;mode=4;
      data_command_end=$realtime;data_ready=$realtime+50.0*TCLK;first_data_edge=1;
     end
     4:begin
      remaining=remaining-1;
      if(remaining==0)begin
       mode=0;regread_count=regread_count+1;command_ready=$realtime+4.0*TCLK;
      end
     end
     5:if(data_bits==24)begin mode=0;command_ready=$realtime+4.0*TCLK;end
    endcase
   end else bits=bits+1;
  end
 end
 final begin
  if(read_count>=500 && regread_count>=2 && sync_count>=500 && write_count>=500 && reset_count>=1 && dac_count>=10)begin
   outfile=$fopen("spi_contract.csv","w");
   $fdisplay(outfile,"metric,observed_ns,limit_ns");
   $fdisplay(outfile,"minimum_sclk_period,%0.3f,%0.3f",min_period,4.0*TCLK);
   $fdisplay(outfile,"minimum_sclk_high,%0.3f,200",min_high);
   $fdisplay(outfile,"minimum_sclk_low,%0.3f,200",min_low);
   $fdisplay(outfile,"minimum_din_setup,%0.3f,50",min_setup);
   $fdisplay(outfile,"minimum_data_delay_t6,%0.3f,%0.3f",min_data_delay,50.0*TCLK);
   $fdisplay(outfile,"minimum_adc_cs_hold,%0.3f,%0.3f",min_cs_hold,8.0*TCLK);
   $fdisplay(outfile,"minimum_sync_to_wakeup,%0.3f,%0.3f",min_sync_delay,24.0*TCLK);
   $fdisplay(outfile,"minimum_wreg_to_command,%0.3f,%0.3f",min_write_delay,4.0*TCLK);
   $fdisplay(outfile,"minimum_rdata_update_margin,%0.3f,0",min_read_margin);
   $fclose(outfile);
   $display("PASS_SPI_CONTRACT rdata=%0d rreg=%0d sync=%0d wreg=%0d clocks=%0d resets=%0d dac=%0d cs_hold=%0d",read_count,regread_count,sync_count,write_count,clock_count,reset_count,dac_count,hold_count);
  end else $display("FAIL_CONTRACT_INCOMPLETE rdata=%0d rreg=%0d sync=%0d wreg=%0d resets=%0d dac=%0d",read_count,regread_count,sync_count,write_count,reset_count,dac_count);
 end
endmodule
