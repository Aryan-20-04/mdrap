// ==============================================================================
// MDRAP FPGA Learning Spike (Phase 22 - Tier 2 Hardware Track)
// Module: mdrap_sequence_gap
// Description: Monotonic sequence-gap and retrograde arrival detector
//              evaluating seq_curr != seq_prev + 1 at 300+ MHz.
// ==============================================================================

`timescale 1ns / 1ps

module mdrap_sequence_gap #(
    parameter SEQ_WIDTH = 64
)(
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire                 valid_in,
    input  wire [SEQ_WIDTH-1:0] seq_in,
    output reg                  is_gap,
    output reg                  is_retrograde,
    output reg                  valid_out
);

    reg [SEQ_WIDTH-1:0] last_seq;
    reg                 has_seen_first;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            last_seq       <= {SEQ_WIDTH{1'b0}};
            has_seen_first <= 1'b0;
            is_gap         <= 1'b0;
            is_retrograde  <= 1'b0;
            valid_out      <= 1'b0;
        end else if (valid_in) begin
            if (!has_seen_first) begin
                last_seq       <= seq_in;
                has_seen_first <= 1'b1;
                is_gap         <= 1'b0;
                is_retrograde  <= 1'b0;
            end else begin
                // Check if sequence arrived out of order (retrograde)
                if (seq_in <= last_seq) begin
                    is_retrograde <= 1'b1;
                    is_gap        <= 1'b0;
                // Check if there is a gap (skipped sequences)
                end else if (seq_in > last_seq + 64'd1) begin
                    is_gap        <= 1'b1;
                    is_retrograde <= 1'b0;
                    last_seq      <= seq_in;
                end else begin
                    is_gap        <= 1'b0;
                    is_retrograde <= 1'b0;
                    last_seq      <= seq_in;
                end
            end
            valid_out <= 1'b1;
        end else begin
            valid_out <= 1'b0;
        end
    end

endmodule
