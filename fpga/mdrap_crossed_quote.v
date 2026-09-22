// ==============================================================================
// MDRAP FPGA Learning Spike (Phase 22 - Tier 2 Hardware Track)
// Module: mdrap_crossed_quote
// Description: Fully combinatorial / 1-cycle pipelined crossed-quote detector
//              evaluating fixed-point Bid >= Ask condition at 300+ MHz.
// ==============================================================================

`timescale 1ns / 1ps

module mdrap_crossed_quote #(
    parameter PRICE_WIDTH = 64  // 64-bit unsigned fixed-point price (scaled 1e8)
)(
    input  wire                   clk,
    input  wire                   rst_n,
    input  wire                   valid_in,
    input  wire [PRICE_WIDTH-1:0] bid_price,
    input  wire [PRICE_WIDTH-1:0] ask_price,
    output reg                    is_crossed,
    output reg                    valid_out
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            is_crossed <= 1'b0;
            valid_out  <= 1'b0;
        end else if (valid_in) begin
            // Single-cycle carry-chain comparator:
            // A crossed quote occurs when bid >= ask (and both prices are non-zero)
            if (bid_price > 64'd0 && ask_price > 64'd0 && bid_price >= ask_price) begin
                is_crossed <= 1'b1;
            end else begin
                is_crossed <= 1'b0;
            end
            valid_out <= 1'b1;
        end else begin
            is_crossed <= 1'b0;
            valid_out  <= 1'b0;
        end
    end

endmodule
