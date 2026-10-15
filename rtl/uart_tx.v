// uart_tx — 8N1 UART transmitter
//
// valid/ready byte interface: a byte is accepted on a clock edge where
// valid && ready. ready is high whenever the transmitter is idle.
//
// Timing: CLKS_PER_BIT = clk_freq / baud (e.g. 100 MHz / 115200 = 868).

module uart_tx #(
    parameter CLKS_PER_BIT = 868
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       valid,
    input  wire [7:0] data,
    output wire       ready,
    output reg        txd           // serial output (idle high)
);

    localparam CW = $clog2(CLKS_PER_BIT + 1);

    reg          busy;
    reg [CW-1:0] clk_cnt;
    reg [3:0]    bit_idx;           // 0 = start, 1..8 = data, 9 = stop
    reg [9:0]    frame;             // {stop, data[7:0], start}, shifted LSB first

    assign ready = ~busy;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy    <= 1'b0;
            clk_cnt <= {CW{1'b0}};
            bit_idx <= 4'd0;
            frame   <= 10'h3FF;
            txd     <= 1'b1;
        end else if (!busy) begin
            txd <= 1'b1;
            if (valid) begin
                busy    <= 1'b1;
                frame   <= {1'b1, data, 1'b0};
                clk_cnt <= {CW{1'b0}};
                bit_idx <= 4'd0;
                txd     <= 1'b0;    // start bit goes out immediately
            end
        end else begin
            if (clk_cnt == CLKS_PER_BIT - 1) begin
                clk_cnt <= {CW{1'b0}};
                if (bit_idx == 4'd9) begin
                    busy <= 1'b0;
                    txd  <= 1'b1;
                end else begin
                    bit_idx <= bit_idx + 4'd1;
                    txd     <= frame[bit_idx + 4'd1];
                end
            end else begin
                clk_cnt <= clk_cnt + 1'b1;
            end
        end
    end

endmodule
