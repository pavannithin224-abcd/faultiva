// A deliberately tiny circuit with a simple_handshake interface, so the whole
// characterization flow runs in about ten seconds.
//
//   reset_n low      -> clear
//   init pulsed high -> start, result = block ^ key[31:0]
//   four cycles      -> rotate result, then raise ready
//
// Nothing about it is cryptographically meaningful.  It exists to exercise the
// pipeline end to end on a circuit small enough to inspect by hand.
module demo_core (
  input  wire        clk,
  input  wire        reset_n,
  input  wire        init,
  output reg         ready,
  input  wire [63:0] key,
  input  wire [31:0] block,
  output reg  [31:0] result
);
  reg [2:0] count;

  always @(posedge clk) begin
    if (!reset_n) begin
      ready  <= 1'b0;
      count  <= 3'd0;
      result <= 32'd0;
    end else if (init) begin
      ready  <= 1'b0;
      count  <= 3'd4;
      result <= block ^ key[31:0];
    end else if (count != 3'd0) begin
      count  <= count - 3'd1;
      result <= {result[30:0], result[31]};
      if (count == 3'd1) ready <= 1'b1;
    end
  end
endmodule
