// uart_rx — 8N1 UART receiver
//
// Samples the (asynchronous) rxd line through a 2-FF synchronizer, detects
// the start bit, then samples each data bit at its midpoint. LSB first.
//
// Timing: CLKS_PER_BIT = clk_freq / baud (e.g. 100 MHz / 115200 = 868).
//
// Outputs:
//   data_valid — 1-cycle pulse when data[7:0] holds a newly received byte
//   frame_err  — 1-cycle pulse (with data_valid) if the stop bit was 0

module uart_rx #(
    parameter CLKS_PER_BIT = 868
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       rxd,          // async serial input (idle high)
    output reg  [7:0] data,
    output reg        data_valid,
    output reg        frame_err
);

    localparam CW = $clog2(CLKS_PER_BIT + 1);

    localparam S_IDLE  = 2'd0;
    localparam S_START = 2'd1;
    localparam S_DATA  = 2'd2;
    localparam S_STOP  = 2'd3;

    // ─── 2-FF synchronizer (idle high) ───────────────────────────
    reg rxd_meta, rxd_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rxd_meta <= 1'b1;
            rxd_sync <= 1'b1;
        end else begin
            rxd_meta <= rxd;
            rxd_sync <= rxd_meta;
        end
    end

    reg [1:0]    state;
    reg [CW-1:0] clk_cnt;
    reg [2:0]    bit_idx;
    reg [7:0]    shift;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            clk_cnt    <= {CW{1'b0}};
            bit_idx    <= 3'd0;
            shift      <= 8'd0;
            data       <= 8'd0;
            data_valid <= 1'b0;
            frame_err  <= 1'b0;
        end else begin
            data_valid <= 1'b0;
            frame_err  <= 1'b0;

            case (state)
            S_IDLE: begin
                clk_cnt <= {CW{1'b0}};
                bit_idx <= 3'd0;
                if (!rxd_sync)
                    state <= S_START;
            end

            // Wait half a bit, confirm start bit is still low
            S_START: begin
                if (clk_cnt == (CLKS_PER_BIT - 1) / 2) begin
                    clk_cnt <= {CW{1'b0}};
                    state   <= rxd_sync ? S_IDLE : S_DATA;  // glitch → back to idle
                end else begin
                    clk_cnt <= clk_cnt + 1'b1;
                end
            end

            // Sample 8 data bits, one full bit period apart
            S_DATA: begin
                if (clk_cnt == CLKS_PER_BIT - 1) begin
                    clk_cnt <= {CW{1'b0}};
                    shift   <= {rxd_sync, shift[7:1]};
                    if (bit_idx == 3'd7)
                        state <= S_STOP;
                    bit_idx <= bit_idx + 3'd1;
                end else begin
                    clk_cnt <= clk_cnt + 1'b1;
                end
            end

            // Sample the stop bit, emit the byte
            S_STOP: begin
                if (clk_cnt == CLKS_PER_BIT - 1) begin
                    clk_cnt    <= {CW{1'b0}};
                    data       <= shift;
                    data_valid <= 1'b1;
                    frame_err  <= ~rxd_sync;
                    state      <= S_IDLE;
                end else begin
                    clk_cnt <= clk_cnt + 1'b1;
                end
            end
            endcase
        end
    end

endmodule
