// evidence_uart_tb — Annotated waveform wrapper around fpga_top (UART path)
//
// Simulation only. Instantiates the unmodified fpga_top with simulation
// scaling (8 clocks per UART bit) and adds human-readable annotation signals
// (ASCII strings: set the viewer's display format to ASCII):
//
//   txn_name          which test transaction is running (driven by the test)
//   rx_bit_name       bit the UART receiver is sampling on uart_rxd:
//                     IDLE / START / D0..D7 / STOP
//   rx_byte, rx_char  last byte received (hex / printable char), with
//   rx_byte_valid     1-cycle pulse when a byte has been received
//   rx_count          bytes received since reset
//   tx_bit_name       bit the UART transmitter is driving on uart_txd
//   tx_byte, tx_char  byte accepted by the transmitter, with
//   tx_byte_start     1-cycle pulse when transmission of a byte starts
//   tx_count          bytes sent since reset
//   host_state_name   uart_host protocol FSM state
//   cmd_name          command being processed ("-" when idle)
//   status_name       status code of the response being built
//
// The VCD path comes from the +vcd=<path> plusarg (see Makefile). Dumped
// scopes: this module, dut (fpga_top wires), dut.u_rx, dut.u_tx, dut.u_host.

module evidence_uart_tb (
    input  wire       clk,
    input  wire       rst_btn,
    input  wire       uart_rxd,
    output wire       uart_txd,
    output wire [3:0] led,
    input  wire [3:0] txn_id          // test transaction marker (0 = none)
);

    fpga_top #(
        .CLK_FREQ      (1_000_000),     // simulation scaling: 1e6 / 125e3 = 8 clocks per bit
        .BAUD          (125_000),
        .ENABLE_KEYGEN (1),
        .ENABLE_ENCAPS (1),
        .ENABLE_DECAPS (1)
    ) dut (
        .clk      (clk),
        .rst_btn  (rst_btn),
        .uart_rxd (uart_rxd),
        .uart_txd (uart_txd),
        .led      (led)
    );

    // ─── Transaction marker ──────────────────────────────────────
    reg [8*14-1:0] txn_name;
    always @(*) begin
        case (txn_id)
            4'd1:    txn_name = "1 PING";
            4'd2:    txn_name = "2 WRITE_POLY";
            4'd3:    txn_name = "3 READ_POLY";
            4'd4:    txn_name = "4 KEYGEN";
            4'd5:    txn_name = "5 READ_SECRET";
            4'd6:    txn_name = "6 BAD_SLOT";
            default: txn_name = "-";
        endcase
    end

    // ─── Receive side (uart_rxd → uart_rx) ───────────────────────
    wire [7:0] rx_byte       = dut.rx_data;
    wire       rx_byte_valid = dut.rx_valid;
    reg  [7:0] rx_char;
    reg [15:0] rx_count = 16'd0;
    always @(posedge clk) if (rx_byte_valid) rx_count <= rx_count + 16'd1;
    always @(*) rx_char = (rx_byte >= 8'h20 && rx_byte < 8'h7F) ? rx_byte : ".";

    reg [8*5-1:0] rx_bit_name;
    always @(*) begin
        case (dut.u_rx.state)
            2'd1:    rx_bit_name = "START";
            2'd2:    rx_bit_name = {"D", 8'h30 + {5'd0, dut.u_rx.bit_idx}};
            2'd3:    rx_bit_name = "STOP";
            default: rx_bit_name = "IDLE";
        endcase
    end

    // ─── Transmit side (uart_tx → uart_txd) ──────────────────────
    wire       tx_byte_start = dut.tx_valid && dut.tx_ready;
    reg  [7:0] tx_byte = 8'd0;
    reg  [7:0] tx_char;
    reg [15:0] tx_count = 16'd0;
    always @(posedge clk)
        if (tx_byte_start) begin
            tx_byte  <= dut.tx_data;
            tx_count <= tx_count + 16'd1;
        end
    always @(*) tx_char = (tx_byte >= 8'h20 && tx_byte < 8'h7F) ? tx_byte : ".";

    reg [8*5-1:0] tx_bit_name;
    always @(*) begin
        if (!dut.u_tx.busy)                 tx_bit_name = "IDLE";
        else if (dut.u_tx.bit_idx == 4'd0)  tx_bit_name = "START";
        else if (dut.u_tx.bit_idx == 4'd9)  tx_bit_name = "STOP";
        else                                tx_bit_name = {"D", 8'h2F + {4'd0, dut.u_tx.bit_idx}};
    end

    // ─── Protocol FSM (uart_host) ────────────────────────────────
    reg [8*9-1:0] host_state_name;
    always @(*) begin
        case (dut.u_host.state)
            4'd0:    host_state_name = "IDLE";
            4'd1:    host_state_name = "ARGS";
            4'd2:    host_state_name = "EXEC";
            4'd3:    host_state_name = "FEED";
            4'd4:    host_state_name = "RUN";
            4'd5:    host_state_name = "WPOLY";
            4'd6:    host_state_name = "DRAIN";
            4'd7:    host_state_name = "RST";
            4'd8:    host_state_name = "TX_STATUS";
            4'd9:    host_state_name = "TX_SETUP";
            4'd10:   host_state_name = "TX_WAIT1";
            4'd11:   host_state_name = "TX_WAIT2";
            4'd12:   host_state_name = "TX_LATCH";
            4'd13:   host_state_name = "TX_SEND";
            default: host_state_name = "?";
        endcase
    end

    reg [8*11-1:0] cmd_name;
    always @(*) begin
        if (dut.u_host.state == 4'd0)
            cmd_name = "-";
        else case (dut.u_host.cmd)
            8'h01:   cmd_name = "PING";
            8'h02:   cmd_name = "RESET";
            8'h10:   cmd_name = "KEYGEN";
            8'h20:   cmd_name = "ENCAPS";
            8'h30:   cmd_name = "DECRYPT";
            8'h40:   cmd_name = "READ_POLY";
            8'h41:   cmd_name = "WRITE_POLY";
            8'h50:   cmd_name = "READ_SECRET";
            default: cmd_name = "UNKNOWN";
        endcase
    end

    reg [8*11-1:0] status_name;
    always @(*) begin
        if (dut.u_host.state == 4'd0)
            status_name = "-";
        else case (dut.u_host.status)
            8'h00:   status_name = "OK";
            8'h01:   status_name = "UNKNOWN_CMD";
            8'h02:   status_name = "NO_CORE";
            8'h03:   status_name = "BAD_ARG";
            8'h04:   status_name = "BUSY";
            8'h05:   status_name = "TIMEOUT";
            8'h06:   status_name = "OVERFLOW";
            default: status_name = "?";
        endcase
    end

    // ─── Waveform dump ───────────────────────────────────────────
    initial begin : vcd_setup
        reg [8*512-1:0] vcd_file;
        if (!$value$plusargs("vcd=%s", vcd_file))
            vcd_file = "evidence_uart.vcd";
        $dumpfile(vcd_file);
        // Icarus repeats the top scope once per $dumpvars call; the Makefile
        // merges the copies afterwards (tb/vcd_merge_scopes.py).
        $dumpvars(1, evidence_uart_tb);
        $dumpvars(1, dut);
        $dumpvars(1, dut.u_rx);
        $dumpvars(1, dut.u_tx);
        $dumpvars(1, dut.u_host);
    end

endmodule
