// Battery REV A only. ADS1256 differential MUX 01/23, 30kSPS, PGA1, BUF off.
// Each MUX change is followed by SYNC/WAKEUP and a NEW DRDY high->low.
// DRDY timestamps are conversion-ready times, not instantaneous apertures.
module battery_adc_engine #(
 parameter integer CLK_HZ=100000000, SPI_DIV=32,
 parameter integer RESET_CYCLES=20000000, SLOT_CYCLES=32768,
 parameter integer TIMEOUT_CYCLES=50000000
)(
 input wire clk, resetn, run, drive, phase_high,
 input wire [15:0] gate_high,
 input wire ads_drdy, spi_miso,
 output reg ads_reset, ads_pdwn, ads_cs, dac_cs, spi_sclk, spi_mosi,
 output reg ready, zero_ack, sample_valid, sample_channel,
 output reg signed [23:0] sample_data,
 output reg [31:0] sample_tick, sample_phase,
 input wire [31:0] tick, phase,
 output reg [31:0] fault,
 output reg [31:0] adc_config, ofc, fsc
);
 localparam IDLE=0, BOOT=1, RESET_LOW=2, RESET_WAIT=3, WAIT_BOOT=4,
  INIT_SELECT=5, INIT_BYTES=6, CAL_SELECT=7, CAL_CMD=8, CAL_HIGH=9, CAL_LOW=10,
  REGS_SELECT=11, REGS_COUNT=12, REGS_DELAY=13, REGS_READ=14, REGS_SAVE=15,
  MUX_SELECT=16, MUX_COUNT=17, MUX_DATA=18, MUX_GAP=19, SYNC_CMD=20,
  SYNC_GAP=21, WAKE_CMD=22, CONV_HIGH=23, CONV_LOW=24,
  READ_CMD=25, READ_DELAY=26, READ0=27, READ1=28, READ2=29, EMIT=30,
  DAC_SELECT=31, DAC_HI=32, DAC_LO=33, DAC_FINISH=34,
  RELEASE=35, SPI_WAIT=36;
 reg [5:0] state, after_spi, after_release, after_dac;
 reg [31:0] timer, slot_timer, wait_timer;
 reg [3:0] index;
 reg channel, run_prev, stop_pending;
 reg [15:0] dac_target, dac_applied;
 reg [7:0] high_byte, mid_byte, tx, rx, received;
 reg [2:0] bit_index;
 reg [15:0] divider;
 reg spi_busy, launch, spi_done, edge_high;
 wire [31:0] wait_limit=(state==CONV_HIGH || state==CONV_LOW)?CLK_HZ/100:TIMEOUT_CYCLES;
 (* ASYNC_REG="TRUE", SHREG_EXTRACT="NO" *) reg drdy_meta, drdy_sync;
 wire enabled=run && ready && fault==0;
 wire [15:0] desired=(enabled && drive && phase_high)?gate_high:16'd0;
 task send(input [7:0] value,input [5:0] next_state);
  begin tx<=value;launch<=1;after_spi<=next_state;state<=SPI_WAIT;end
 endtask
 task release_adc(input [5:0] next_state);
  begin timer<=0;after_release<=next_state;state<=RELEASE;end
 endtask
 function [7:0] init_byte(input [3:0] n);
  case(n)
   0:init_byte=8'h0f; // SDATAC
   1:init_byte=8'h50; 2:init_byte=8'h03;
   3:init_byte=8'h00; // MSB first, ACAL=0, BUFEN=0
   4:init_byte=8'h01; 5:init_byte=8'h00; 6:init_byte=8'hf0;
   default:init_byte=0;
  endcase
 endfunction
 always @(posedge clk) begin
  if(!resetn) begin
   spi_sclk<=0;spi_mosi<=0;spi_busy<=0;spi_done<=0;divider<=0;
   bit_index<=7;rx<=0;received<=0;edge_high<=0;
  end else begin
   spi_done<=0;
   if(launch && !spi_busy) begin
    spi_busy<=1;divider<=0;bit_index<=7;rx<=0;edge_high<=0;
   end else if(spi_busy) begin
    if(divider==SPI_DIV-1) begin
     divider<=0;
     if(!edge_high) begin spi_sclk<=1;spi_mosi<=tx[bit_index];edge_high<=1;end
     else begin
      spi_sclk<=0;rx[bit_index]<=spi_miso;edge_high<=0;
      if(bit_index==0) begin spi_busy<=0;spi_done<=1;received<={rx[7:1],spi_miso};end
      else bit_index<=bit_index-1'b1;
     end
    end else divider<=divider+1'b1;
   end
  end
 end
 always @(posedge clk) begin
  if(!resetn) begin
   drdy_meta<=1;drdy_sync<=1;
   state<=BOOT;after_spi<=0;after_release<=0;after_dac<=0;
   timer<=0;slot_timer<=0;wait_timer<=0;index<=0;channel<=0;
   run_prev<=0;stop_pending<=0;dac_target<=0;dac_applied<=16'hffff;
   high_byte<=0;mid_byte<=0;tx<=0;launch<=0;
   ads_reset<=1;ads_pdwn<=1;ads_cs<=1;dac_cs<=1;
   ready<=0;zero_ack<=0;sample_valid<=0;sample_channel<=0;sample_data<=0;
   sample_tick<=0;sample_phase<=0;fault<=0;adc_config<=0;ofc<=0;fsc<=0;
  end else begin
   drdy_meta<=ads_drdy;drdy_sync<=drdy_meta;
   launch<=0;sample_valid<=0;run_prev<=run;
   timer<=timer+1'b1;
   if(slot_timer<SLOT_CYCLES) slot_timer<=slot_timer+1'b1;
   if(run && !run_prev) begin channel<=0;slot_timer<=SLOT_CYCLES;end
   if(!run && run_prev) stop_pending<=1;
   if(state==CAL_HIGH || state==CAL_LOW || state==WAIT_BOOT ||
      state==CONV_HIGH || state==CONV_LOW) begin
    wait_timer<=wait_timer+1'b1;
   end else wait_timer<=0;
   // Abort only at a byte boundary; keep ADS CS hold time before the zero frame.
   // A DAC frame already in flight completes, then the queued zero frame wins.
   if(wait_timer>=wait_limit-1 && (state==CAL_HIGH || state==CAL_LOW || state==WAIT_BOOT || state==CONV_HIGH || state==CONV_LOW)) begin
    fault<=fault | 32'h1;stop_pending<=1;ready<=0;release_adc(IDLE);
   end else if(stop_pending && state!=SPI_WAIT && state!=RELEASE &&
      !(state>=DAC_SELECT && state<=DAC_FINISH)) begin
    stop_pending<=0;dac_target<=0;after_dac<=IDLE;release_adc(DAC_SELECT);
   end else case(state)
    BOOT: begin dac_target<=0;after_dac<=RESET_LOW;state<=DAC_SELECT;end
    RESET_LOW: begin
     ads_reset<=0;
     if(timer>=RESET_CYCLES) begin ads_reset<=1;timer<=0;state<=RESET_WAIT;end
    end
    RESET_WAIT: if(timer>=RESET_CYCLES) state<=WAIT_BOOT;
    WAIT_BOOT: if(!drdy_sync) state<=INIT_SELECT;
    INIT_SELECT: begin ads_cs<=0;index<=0;send(init_byte(0),INIT_BYTES);end
    INIT_BYTES: begin
     if(index==6) release_adc(CAL_SELECT);
     else begin index<=index+1'b1;send(init_byte(index+1'b1),INIT_BYTES);end
    end
    CAL_SELECT: begin ads_cs<=0;send(8'hf0,CAL_CMD);end
    CAL_CMD: release_adc(CAL_HIGH);
    CAL_HIGH: if(drdy_sync) state<=CAL_LOW;
    CAL_LOW: if(!drdy_sync) state<=REGS_SELECT;
    REGS_SELECT: begin ads_cs<=0;send(8'h10,REGS_COUNT);end
    REGS_COUNT: send(8'h0a,REGS_DELAY);
    REGS_DELAY: begin timer<=0;index<=0;state<=REGS_READ;end
    REGS_READ: if(timer>=700) send(0,REGS_SAVE);
    REGS_SAVE: begin
     case(index)
      0:adc_config[7:0]<=received;
      1:adc_config[15:8]<=received;
      2:adc_config[23:16]<=received;
      3:adc_config[31:24]<=received;
      5:ofc[7:0]<=received;6:ofc[15:8]<=received;7:ofc[23:16]<=received;
      8:fsc[7:0]<=received;9:fsc[15:8]<=received;10:fsc[23:16]<=received;
     endcase
     if(index==10) begin
      if(adc_config[31:8]!=24'hf00001 || adc_config[7:1]!=7'h18) begin
       fault<=fault | 32'h2;ready<=0;
      end else ready<=1;
      release_adc(IDLE);
     end else begin index<=index+1'b1;send(0,REGS_SAVE);end
    end
    IDLE: begin
     ads_cs<=1;dac_cs<=1;
     if(desired!=dac_applied && timer>=20) begin dac_target<=desired;after_dac<=IDLE;state<=DAC_SELECT;end
     else if(enabled && slot_timer>=SLOT_CYCLES) begin
      slot_timer<=0;state<=MUX_SELECT;
     end
    end
    MUX_SELECT: begin ads_cs<=0;send(8'h51,MUX_COUNT);end
    MUX_COUNT: send(0,MUX_DATA);
    MUX_DATA: send(channel?8'h23:8'h01,MUX_GAP);
    MUX_GAP: begin timer<=0;state<=SYNC_CMD;end
    SYNC_CMD: if(timer>=100) send(8'hfc,SYNC_GAP);
    SYNC_GAP: begin timer<=0;state<=WAKE_CMD;end
    WAKE_CMD: if(timer>=400) send(0,CONV_HIGH);
    CONV_HIGH: if(drdy_sync) begin release_adc(CONV_LOW);end
    CONV_LOW: if(!drdy_sync) begin
     sample_tick<=tick;sample_phase<=phase;state<=READ_CMD;
    end
    READ_CMD: begin ads_cs<=0;send(8'h01,READ_DELAY);end
    READ_DELAY: begin timer<=0;state<=READ0;end
    READ0: if(timer>=700) send(0,READ1);
    READ1: begin high_byte<=received;send(0,READ2);end
    READ2: begin mid_byte<=received;send(0,EMIT);end
    EMIT: begin
     if(enabled) begin sample_data<={high_byte,mid_byte,received};sample_channel<=channel;sample_valid<=1;end
     channel<=~channel;release_adc(IDLE);
    end
    DAC_SELECT: begin dac_cs<=0;zero_ack<=0;send(8'h10,DAC_HI);end
    DAC_HI: send(dac_target[15:8],DAC_LO);
    DAC_LO: send(dac_target[7:0],DAC_FINISH);
    DAC_FINISH: begin
     // One fabric cycle + following idle provides >50 ns before next frame.
     // The normal ADC slot is >300 us; emergency stop enters ADC release first.
     dac_cs<=1;dac_applied<=dac_target;zero_ack<=dac_target==0;
     timer<=0;state<=after_dac;
    end
    RELEASE: if(timer>=200) begin ads_cs<=1;state<=after_release;end
    SPI_WAIT: if(spi_done) state<=after_spi;
    default: begin fault<=fault|32'h4;stop_pending<=1;state<=IDLE;end
   endcase
  end
 end
endmodule
