// GPIO mailbox ABI BE03: 2..100Hz, <=4s capture, 16384 raw records.
// Read frozen RAM/stats only after DONE && ZERO.
// PL owns square-wave timing, ADC SPI/MUX, timestamps, raw RAM, I/Q moments,
// watchdog, finite capture and threshold trips. PS owns calibrated V/I and UI.
module battery_eis_core #(
 parameter integer CLK_HZ=100000000, RESET_CYCLES=20000000,
 parameter integer SLOT_CYCLES=32768, WATCHDOG_CYCLES=50000000
)(
 input wire clk_100m,resetn,
 input wire [31:0] phase_inc,control,
 input wire ads_drdy,spi_miso,
 output wire ads_reset,ads_pdwn,ads_cs,dac_cs,spi_sclk,spi_mosi,
 output reg [31:0] left_i,left_q,right_i,right_q,
 output wire [31:0] status
);
 wire clk=clk_100m;
 reg [31:0] increment,settle_ticks,capture_ticks,min_v,max_v,max_i;
 reg [15:0] gate_high;
 reg active,done,run_prev,cmd_seen,hb_seen;
 reg [7:0] sequence_id;
 reg [31:0] phase,tick,watchdog,local_fault;
 reg [14:0] count;
 localparam integer RAW_CAPACITY=16384;
 localparam [31:0] MIN_INCREMENT=(64'd8589934592+CLK_HZ/2)/CLK_HZ;
 localparam [31:0] MAX_INCREMENT=(64'd429496729600+CLK_HZ/2)/CLK_HZ;
 wire engine_ready,zero_ack,sample_valid,sample_channel;
 wire signed [23:0] sample_data;
 wire [31:0] sample_tick,sample_phase,engine_fault,adc_config,ofc,fsc;
 wire [31:0] faults=local_fault|engine_fault;
 wire [3:0] command=control[26:23];
 wire [5:0] page=control[21:16];
 reg [2:0] drain;
 reg flush;
 wire [575:0] v_stats,i_stats;
 wire moments_valid,moments_invalid;
 wire accepted=sample_valid && active && sample_tick>=settle_ticks && count<RAW_CAPACITY;
 (* ram_style="block" *) reg [127:0] raw[0:RAW_CAPACITY-1];
 reg [127:0] read_record;
 always @(posedge clk) read_record<=raw[control[13:0]];
 always @(posedge clk) if(accepted)
  raw[count[13:0]]<={31'd0,sample_channel,{{8{sample_data[23]}},sample_data},sample_phase,sample_tick};
 battery_adc_engine #(.CLK_HZ(CLK_HZ),.RESET_CYCLES(RESET_CYCLES),.SLOT_CYCLES(SLOT_CYCLES)) engine(
  .clk(clk),.resetn(resetn && !control[29]),.run(active),.drive(control[30]),
  .phase_high(!phase[31]),.gate_high(gate_high),.ads_drdy(ads_drdy),.spi_miso(spi_miso),
  .ads_reset(ads_reset),.ads_pdwn(ads_pdwn),.ads_cs(ads_cs),.dac_cs(dac_cs),
  .spi_sclk(spi_sclk),.spi_mosi(spi_mosi),.ready(engine_ready),.zero_ack(zero_ack),
  .sample_valid(sample_valid),.sample_channel(sample_channel),.sample_data(sample_data),
  .sample_tick(sample_tick),.sample_phase(sample_phase),.tick(tick),.phase(phase),
  .fault(engine_fault),.adc_config(adc_config),.ofc(ofc),.fsc(fsc));
 five_electrode_lockin moments(
  .clk(clk),.rst(!resetn || (control[31] && !run_prev)),
  .sample_valid(accepted),.sample_channel(sample_channel),.sample_data(sample_data),
  .sample_phase(sample_phase),.window_tick(flush),.result_valid(moments_valid),
  .left_i(),.left_q(),.right_i(),.right_q(),.left_count(),.right_count(),.overflow(),
  .left_stats(v_stats),.right_stats(i_stats),.stats_invalid(moments_invalid));
 assign status={16'hbe03,sequence_id,cmd_seen,moments_invalid,local_fault[5],zero_ack,
                (faults!=0),done,active,engine_ready};
 always @* begin
  left_i=0;left_q=0;right_i=0;right_q=0;
  if(!control[22]) begin
   left_i=read_record[31:0];left_q=read_record[63:32];
   right_i=read_record[95:64];right_q=read_record[127:96];
  end else if(page<9) begin
   left_i=v_stats[page*64+:32];left_q=v_stats[page*64+32+:32];
   right_i=i_stats[page*64+:32];right_q=i_stats[page*64+32+:32];
  end else case(page)
   9:begin left_i=count;left_q=tick;right_i=faults;right_q=sequence_id;end
   10:begin left_i=adc_config;left_q=ofc;right_i=fsc;right_q=SLOT_CYCLES;end
   11:begin left_i=increment;left_q=settle_ticks;right_i=capture_ticks;right_q=gate_high;end
   12:begin left_i=RAW_CAPACITY;left_q=MIN_INCREMENT;right_i=MAX_INCREMENT;right_q=4*CLK_HZ;end
  endcase
 end
 always @(posedge clk) begin
  if(!resetn) begin
   increment<=3436;settle_ticks<=25000000;capture_ticks<=100000000;
   min_v<=0;max_v<=8388606;max_i<=120000;gate_high<=65535;
   active<=0;done<=0;run_prev<=0;cmd_seen<=0;hb_seen<=0;sequence_id<=0;
   phase<=0;tick<=0;watchdog<=0;local_fault<=0;count<=0;drain<=0;flush<=0;
  end else begin
   run_prev<=control[31];hb_seen<=control[28];flush<=0;
   if(control[27]!=cmd_seen) begin
    cmd_seen<=control[27];
    if(!active && zero_ack) case(command)
     1:increment<=phase_inc;2:settle_ticks<=phase_inc;3:capture_ticks<=phase_inc;
     4:min_v<=phase_inc;5:max_v<=phase_inc;6:max_i<=phase_inc;7:gate_high<=phase_inc[15:0];
    endcase
   end
   if(control[29] && !active) begin done<=0;local_fault<=0;end
   if(control[31] && !run_prev) begin
    local_fault<=0;done<=0;count<=0;phase<=0;tick<=0;watchdog<=0;drain<=0;
    // Same 100MHz production limits; CLK_HZ also permits a bounded core-only test.
    if(engine_ready && zero_ack && increment>=MIN_INCREMENT && increment<=MAX_INCREMENT &&
       capture_ticks>=CLK_HZ/5 && capture_ticks<=4*CLK_HZ && settle_ticks<=CLK_HZ &&
       min_v>0 && max_v>min_v && max_v<=8388606 && max_i>0 && max_i<=120000 &&
       gate_high>=60000) begin active<=1;sequence_id<=sequence_id+1'b1;end
    else begin local_fault<=32'h100;drain<=3;end
   end
   if(active) begin
    phase<=phase+increment;tick<=tick+1'b1;
    if(control[28]!=hb_seen)watchdog<=0;else watchdog<=watchdog+1'b1;
    if(accepted)count<=count+1'b1;
    if(sample_valid) begin
     if(sample_data==24'sh7fffff || sample_data==24'sh800000) local_fault<=local_fault|32'h10;
     else if(!sample_channel && ($signed(sample_data)<$signed(min_v) || $signed(sample_data)>$signed(max_v)))
      local_fault<=local_fault|32'h40;
     // 168 codes is about 100uV, i.e. nominal -1mA with the final 0.1ohm shunt.
     else if(sample_channel && ($signed(sample_data)>$signed(max_i) || $signed(sample_data)< -32'sd168))
      local_fault<=local_fault|32'h80;
    end
    if(count>=RAW_CAPACITY) local_fault<=local_fault|32'h20;
    if(watchdog>=WATCHDOG_CYCLES-1) local_fault<=local_fault|32'h8;
    if(!control[31] || faults!=0 || tick>=settle_ticks+capture_ticks-1) begin
     active<=0;drain<=3;
     if(!control[31])local_fault<=local_fault|32'h200;
    end
   end
   if(drain!=0) begin
    drain<=drain-1'b1;
    if(drain==1)flush<=1;
   end
   if(moments_valid)done<=1;
  end
 end
endmodule
