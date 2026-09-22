// ==============================================================================
// MDRAP FPGA Learning Spike (Phase 22 - Tier 2 Hardware Track)
// Testbench: tb_mdrap_rules
// Description: Self-checking testbench for crossed quote and sequence gap modules.
// ==============================================================================

`timescale 1ns / 1ps

module tb_mdrap_rules;

    reg clk;
    reg rst_n;
    reg valid_in;
    reg [63:0] bid_price;
    reg [63:0] ask_price;
    reg [63:0] seq_in;

    wire is_crossed;
    wire crossed_valid;
    wire is_gap;
    wire is_retrograde;
    wire gap_valid;

    integer errors = 0;

    // Instantiate Crossed Quote Unit
    mdrap_crossed_quote #(.PRICE_WIDTH(64)) u_crossed (
        .clk(clk),
        .rst_n(rst_n),
        .valid_in(valid_in),
        .bid_price(bid_price),
        .ask_price(ask_price),
        .is_crossed(is_crossed),
        .valid_out(crossed_valid)
    );

    // Instantiate Sequence Gap Unit
    mdrap_sequence_gap #(.SEQ_WIDTH(64)) u_gap (
        .clk(clk),
        .rst_n(rst_n),
        .valid_in(valid_in),
        .seq_in(seq_in),
        .is_gap(is_gap),
        .is_retrograde(is_retrograde),
        .valid_out(gap_valid)
    );

    // 300 MHz clock generation (period ~ 3.333 ns)
    always #1.666 clk = ~clk;

    initial begin
        clk = 0;
        rst_n = 0;
        valid_in = 0;
        bid_price = 0;
        ask_price = 0;
        seq_in = 0;

        #10;
        rst_n = 1;
        #10;

        // Test 1: Normal quote (Bid=100.00, Ask=100.10, Seq=1)
        @(posedge clk);
        valid_in = 1;
        bid_price = 64'd10000000000;
        ask_price = 64'd10010000000;
        seq_in    = 64'd1;

        @(posedge clk);
        // Test 2: Crossed quote (Bid=100.20, Ask=100.10, Seq=2)
        bid_price = 64'd10020000000;
        ask_price = 64'd10010000000;
        seq_in    = 64'd2;

        @(posedge clk);
        #1;
        if (is_crossed !== 1'b0) begin
            $display("[FAIL] Test 1 expected uncrossed");
            errors = errors + 1;
        end

        @(posedge clk);
        // Test 3: Sequence Gap (Seq=5, jump from 2)
        bid_price = 64'd10000000000;
        ask_price = 64'd10010000000;
        seq_in    = 64'd5;

        @(posedge clk);
        #1;
        if (is_crossed !== 1'b1) begin
            $display("[FAIL] Test 2 expected crossed quote");
            errors = errors + 1;
        end

        @(posedge clk);
        // Test 4: Retrograde (Seq=4 <= 5)
        seq_in = 64'd4;

        @(posedge clk);
        #1;
        if (is_gap !== 1'b1) begin
            $display("[FAIL] Test 3 expected sequence gap");
            errors = errors + 1;
        end

        @(posedge clk);
        #1;
        if (is_retrograde !== 1'b1) begin
            $display("[FAIL] Test 4 expected retrograde");
            errors = errors + 1;
        end

        valid_in = 0;
        #20;

        if (errors == 0) begin
            $display("[PASS] All FPGA RTL assertions verified with zero errors.");
        end else begin
            $display("[FAIL] Total errors: %d", errors);
        end
        $finish;
    end

endmodule
