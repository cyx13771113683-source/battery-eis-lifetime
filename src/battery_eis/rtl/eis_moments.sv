// Board-independent lock-in sufficient-statistic accumulator.
// ARM solves x = DC + I*sin + Q*cos; FPGA owns phase-coherent products.
// Legacy I/Q outputs retained for regression; stats are lossless.
module five_electrode_lockin #(parameter integer OUTPUT_SHIFT=18)(
 input wire clk,rst,sample_valid,sample_channel,
 input wire signed [23:0] sample_data,
 input wire [31:0] sample_phase,
 input wire window_tick,
 output reg result_valid,
 output reg signed [31:0] left_i,left_q,right_i,right_q,
 output reg [15:0] left_count,right_count,
 output reg overflow,
 output reg [575:0] left_stats,right_stats,
 output reg stats_invalid
);
 reg signed [13:0] lut [0:1023];
 initial $readmemh("sine14.mem",lut);
 wire [9:0] sin_addr=sample_phase[31:22];
 wire [9:0] cos_addr=sin_addr+10'd256;
 wire signed [13:0] s=lut[sin_addr],c=lut[cos_addr];
 wire signed [27:0] ss=s*s,cc=c*c,sc=s*c;
 wire signed [37:0] xs=sample_data*s,xc=sample_data*c;
 reg signed [63:0] acc[0:1][0:8];
 reg signed [63:0] product[0:8];
 reg valid_pipe,channel_pipe,clip_pipe,invalid_window;
 reg signed [63:0] next_acc[0:1][0:8];
 reg next_invalid;
 integer ch,j;
 function automatic signed [31:0] scaled(input signed [63:0] value);
  reg signed [63:0] shifted;
  begin
   shifted=value>>>OUTPUT_SHIFT;
   if(shifted>64'sh7fffffff) scaled=32'sh7fffffff;
   else if(shifted< -64'sh80000000) scaled=32'sh80000000;
   else scaled=shifted[31:0];
  end
 endfunction
 function automatic saturated(input signed [63:0] value);
  begin saturated=((value>>>OUTPUT_SHIFT)>64'sh7fffffff)||
                   ((value>>>OUTPUT_SHIFT)< -64'sh80000000);end
 endfunction
 // Include the in-flight product in the ending window. A sample accepted on
 // the tick itself belongs to the next window. No idle cycle is required.
 always @* begin
  for(integer k=0;k<2;k=k+1)
   for(integer m=0;m<9;m=m+1) next_acc[k][m]=acc[k][m];
  next_invalid=invalid_window;
  if(valid_pipe) begin
   if(acc[channel_pipe][0]>=65535) next_invalid=1;
   else for(integer m=0;m<9;m=m+1)
    next_acc[channel_pipe][m]=acc[channel_pipe][m]+product[m];
   if(clip_pipe) next_invalid=1;
  end
 end
 always @(posedge clk) begin
  if(rst) begin
   for(ch=0;ch<2;ch=ch+1) for(j=0;j<9;j=j+1) acc[ch][j]<=0;
   for(j=0;j<9;j=j+1) product[j]<=0;
   valid_pipe<=0;channel_pipe<=0;clip_pipe<=0;invalid_window<=0;
   result_valid<=0;left_i<=0;left_q<=0;right_i<=0;right_q<=0;
   left_count<=0;right_count<=0;overflow<=0;stats_invalid<=0;
   left_stats<=0;right_stats<=0;
  end else begin
   valid_pipe<=sample_valid;result_valid<=window_tick;
   if(sample_valid) begin
    channel_pipe<=sample_channel;
    clip_pipe<=(sample_data==24'sh7fffff || sample_data==24'sh800000);
    product[0]<=1;product[1]<=sample_data;product[2]<=s;product[3]<=c;
    product[4]<=ss;product[5]<=cc;product[6]<=sc;product[7]<=xs;product[8]<=xc;
   end
   if(window_tick) begin
    for(j=0;j<9;j=j+1) begin
     left_stats[j*64+:64]<=next_acc[0][j];right_stats[j*64+:64]<=next_acc[1][j];
    end
    left_i<=scaled(next_acc[0][7]);left_q<=scaled(next_acc[0][8]);
    right_i<=scaled(next_acc[1][7]);right_q<=scaled(next_acc[1][8]);
    left_count<=next_acc[0][0][15:0];right_count<=next_acc[1][0][15:0];
    stats_invalid<=next_invalid;
    overflow<=next_invalid || saturated(next_acc[0][7]) || saturated(next_acc[0][8]) ||
              saturated(next_acc[1][7]) || saturated(next_acc[1][8]);
    for(ch=0;ch<2;ch=ch+1) for(j=0;j<9;j=j+1) acc[ch][j]<=0;
    invalid_window<=0;
   end else begin
    for(ch=0;ch<2;ch=ch+1) for(j=0;j<9;j=j+1) acc[ch][j]<=next_acc[ch][j];
    invalid_window<=next_invalid;
   end
  end
 end
endmodule
