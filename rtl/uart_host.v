// uart_host — UART command processor for the ML-KEM-768 cores
//
// Turns a simple byte-oriented request/response protocol into core control,
// payload streaming, and polynomial bank access. One request is processed at
// a time; every request gets exactly one response (except silently aborted
// ones, see RX timeout below).
//
// Request:  [cmd] [args...] [payload...]
// Response: [status] [payload...]            multi-byte values little-endian
//
//   cmd   name        args          payload in      response payload (status=OK)
//   0x01  PING        -             -               'K' 'Y' version caps
//   0x02  RESET       -             -               -
//   0x10  KEYGEN      -             d (32 B)        cycles (u32)
//   0x20  ENCAPS      -             m||ek (1216 B)  cycles (u32)
//   0x30  DECRYPT     -             -               cycles (u32)
//   0x40  READ_POLY   core, slot    -               256 x u16 coefficients
//   0x41  WRITE_POLY  core, slot    256 x u16       -
//   0x50  READ_SECRET core          -               32 B (core 0: rho, 1: K)
//
//   core: 0 = auto_keygen_top, 1 = auto_encaps_top, 2 = decaps_top
//   caps: bit i set when core i is present in this bitstream
//   cycles: clock cycles from the last payload byte (or start) to done
//
// Status codes:
//   0x00 OK, 0x01 unknown command, 0x02 core not present, 0x03 bad argument,
//   0x04 core busy, 0x05 core timeout (core is reset), 0x06 RX overflow
//   (core is reset). Payload bytes of rejected requests are still drained so
//   the byte stream stays in sync.
//
// RX timeout: if a request stalls mid-frame for RX_TIMEOUT cycles, the
// partial request is dropped (no response) and the cores are reset.
//
// Payload bytes are forwarded to the core through a 1-byte holding register.
// The cores accept a byte within a few dozen cycles, far less than one UART
// byte time, so overflow indicates a hardware fault rather than a normal case.

module uart_host #(
    parameter [2:0]  CAPS          = 3'b111,
    parameter [31:0] RX_TIMEOUT    = 32'd10_000_000,   // 100 ms @ 100 MHz
    parameter [31:0] RUN_TIMEOUT   = 32'd10_000_000
) (
    input  wire        clk,
    input  wire        rst_n,

    // UART byte streams
    input  wire [7:0]  rx_data,
    input  wire        rx_valid,
    output wire [7:0]  tx_data,
    output wire        tx_valid,
    input  wire        tx_ready,

    // Soft reset for the cores (active low)
    output wire        core_rst_n,

    // Core 0: auto_keygen_top
    output wire        kg_start,
    input  wire        kg_done,
    input  wire        kg_busy,
    output wire        kg_in_valid,
    input  wire        kg_in_ready,
    output wire        kg_we,
    input  wire [11:0] kg_dout,
    input  wire [7:0]  kg_secret,       // rho byte at secret_idx

    // Core 1: auto_encaps_top
    output wire        en_start,
    input  wire        en_done,
    input  wire        en_busy,
    output wire        en_in_valid,
    input  wire        en_in_ready,
    output wire        en_we,
    input  wire [11:0] en_dout,
    input  wire [7:0]  en_secret,       // K byte at secret_idx

    // Core 2: decaps_top (K-PKE.Decrypt)
    output wire        de_start,
    input  wire        de_done,
    input  wire        de_busy,
    output wire        de_we,
    input  wire [11:0] de_dout,

    // Shared buses to all cores
    output wire [7:0]  in_data,         // payload byte stream
    output reg  [4:0]  host_slot,
    output reg  [7:0]  host_addr,
    output reg  [11:0] host_din,
    output reg  [4:0]  secret_idx,

    // Status
    output wire        active,          // a request is in progress
    output reg         last_err         // last response was not OK
);

    localparam [7:0] VERSION = 8'h01;

    // ─── Commands ────────────────────────────────────────────────
    localparam [7:0] CMD_PING        = 8'h01;
    localparam [7:0] CMD_RESET       = 8'h02;
    localparam [7:0] CMD_KEYGEN      = 8'h10;
    localparam [7:0] CMD_ENCAPS      = 8'h20;
    localparam [7:0] CMD_DECRYPT     = 8'h30;
    localparam [7:0] CMD_READ_POLY   = 8'h40;
    localparam [7:0] CMD_WRITE_POLY  = 8'h41;
    localparam [7:0] CMD_READ_SECRET = 8'h50;

    // ─── Status codes ────────────────────────────────────────────
    localparam [7:0] ST_OK          = 8'h00;
    localparam [7:0] ST_UNKNOWN_CMD = 8'h01;
    localparam [7:0] ST_NO_CORE     = 8'h02;
    localparam [7:0] ST_BAD_ARG     = 8'h03;
    localparam [7:0] ST_BUSY        = 8'h04;
    localparam [7:0] ST_TIMEOUT     = 8'h05;
    localparam [7:0] ST_OVERFLOW    = 8'h06;

    // ─── Response payload kinds ──────────────────────────────────
    localparam [2:0] RK_NONE   = 3'd0;
    localparam [2:0] RK_PING   = 3'd1;
    localparam [2:0] RK_CYCLES = 3'd2;
    localparam [2:0] RK_POLY   = 3'd3;
    localparam [2:0] RK_SECRET = 3'd4;

    // ─── Payload sizes ───────────────────────────────────────────
    localparam [10:0] LEN_KEYGEN = 11'd32;
    localparam [10:0] LEN_ENCAPS = 11'd1216;
    localparam [10:0] LEN_POLY   = 11'd512;

    localparam NUM_SLOTS = 20;

    // ─── FSM states ──────────────────────────────────────────────
    localparam [3:0] S_IDLE      = 4'd0;
    localparam [3:0] S_ARGS      = 4'd1;
    localparam [3:0] S_EXEC      = 4'd2;
    localparam [3:0] S_FEED      = 4'd3;
    localparam [3:0] S_RUN       = 4'd4;
    localparam [3:0] S_WPOLY     = 4'd5;
    localparam [3:0] S_DRAIN     = 4'd6;
    localparam [3:0] S_RST       = 4'd7;
    localparam [3:0] S_TX_STATUS = 4'd8;
    localparam [3:0] S_TX_SETUP  = 4'd9;
    localparam [3:0] S_TX_WAIT1  = 4'd10;
    localparam [3:0] S_TX_WAIT2  = 4'd11;
    localparam [3:0] S_TX_LATCH  = 4'd12;
    localparam [3:0] S_TX_SEND   = 4'd13;

    reg [3:0]  state;
    reg [7:0]  cmd;
    reg [7:0]  arg0, arg1;
    reg [1:0]  arg_cnt;
    reg [1:0]  sel;             // selected core
    reg [7:0]  status;
    reg [2:0]  resp_kind;
    reg [10:0] resp_len;
    reg [10:0] p_idx;           // response payload byte index
    reg [10:0] total;           // expected payload length
    reg [10:0] rx_cnt;          // payload bytes received
    reg [10:0] cnt;             // payload bytes consumed by the core
    reg [4:0]  rst_cnt;         // soft-reset pulse length
    reg [31:0] cycles;
    reg [31:0] idle_cnt;        // cycles since last RX byte
    reg        pend;            // holding register full
    reg [7:0]  pend_data;
    reg [7:0]  lo_byte;
    reg [7:0]  tx_byte;
    reg        start_r;
    reg        we_r;
    reg        rst_r;
    reg        need_rst;        // reset cores before responding
    reg        abort;           // reset cores, then drop the request silently

    // ─── Selected-core views ─────────────────────────────────────
    wire        sel_present = CAPS[sel];
    wire        sel_busy    = (sel == 2'd0) ? kg_busy :
                              (sel == 2'd1) ? en_busy :
                              (sel == 2'd2) ? de_busy : 1'b0;
    wire        sel_done    = (sel == 2'd0) ? kg_done :
                              (sel == 2'd1) ? en_done :
                              (sel == 2'd2) ? de_done : 1'b0;
    wire        sel_ready   = (sel == 2'd0) ? kg_in_ready :
                              (sel == 2'd1) ? en_in_ready : 1'b0;
    wire [11:0] sel_dout    = (sel == 2'd0) ? kg_dout :
                              (sel == 2'd1) ? en_dout :
                              (sel == 2'd2) ? de_dout : 12'd0;
    wire [7:0]  sel_secret  = (sel == 2'd0) ? kg_secret :
                              (sel == 2'd1) ? en_secret : 8'd0;

    // Core-index validity for commands that take a core argument
    wire        arg_core_ok = (arg0 < 8'd3) && CAPS[arg0[1:0]];

    // ─── Outputs ─────────────────────────────────────────────────
    wire feeding = (state == S_FEED) && pend;
    assign kg_in_valid = feeding && (sel == 2'd0);
    assign en_in_valid = feeding && (sel == 2'd1);
    assign in_data     = pend_data;

    assign kg_start = start_r && (sel == 2'd0);
    assign en_start = start_r && (sel == 2'd1);
    assign de_start = start_r && (sel == 2'd2);

    assign kg_we = we_r && (sel == 2'd0);
    assign en_we = we_r && (sel == 2'd1);
    assign de_we = we_r && (sel == 2'd2);

    assign core_rst_n = ~rst_r;

    assign tx_valid = (state == S_TX_STATUS) || (state == S_TX_SEND);
    assign tx_data  = (state == S_TX_STATUS) ? status : tx_byte;

    assign active = (state != S_IDLE);

    // Response payload byte for index p_idx
    reg [7:0] payload_byte;
    always @(*) begin
        case (resp_kind)
            RK_PING: case (p_idx[1:0])
                         2'd0:    payload_byte = 8'h4B;           // 'K'
                         2'd1:    payload_byte = 8'h59;           // 'Y'
                         2'd2:    payload_byte = VERSION;
                         default: payload_byte = {5'd0, CAPS};
                     endcase
            RK_CYCLES: payload_byte = cycles[p_idx[1:0]*8 +: 8];
            RK_POLY:   payload_byte = p_idx[0] ? {4'd0, sel_dout[11:8]}
                                               : sel_dout[7:0];
            RK_SECRET: payload_byte = sel_secret;
            default:   payload_byte = 8'd0;
        endcase
    end

    // Number of argument bytes per command
    function [1:0] nargs;
        input [7:0] c;
        case (c)
            CMD_READ_POLY, CMD_WRITE_POLY: nargs = 2'd2;
            CMD_READ_SECRET:               nargs = 2'd1;
            default:                       nargs = 2'd0;
        endcase
    endfunction

    wire rx_stalled = (idle_cnt >= RX_TIMEOUT);
    wire consume    = feeding && sel_ready;

    // ─── Main FSM ────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            cmd       <= 8'd0;
            arg0      <= 8'd0;
            arg1      <= 8'd0;
            arg_cnt   <= 2'd0;
            sel       <= 2'd0;
            status    <= ST_OK;
            resp_kind <= RK_NONE;
            resp_len  <= 11'd0;
            p_idx     <= 11'd0;
            total     <= 11'd0;
            rx_cnt    <= 11'd0;
            cnt       <= 11'd0;
            rst_cnt   <= 5'd0;
            cycles    <= 32'd0;
            idle_cnt  <= 32'd0;
            pend      <= 1'b0;
            pend_data <= 8'd0;
            lo_byte   <= 8'd0;
            tx_byte   <= 8'd0;
            start_r   <= 1'b0;
            we_r      <= 1'b0;
            rst_r     <= 1'b0;
            need_rst  <= 1'b0;
            abort     <= 1'b0;
            host_slot <= 5'd0;
            host_addr <= 8'd0;
            host_din  <= 12'd0;
            secret_idx <= 5'd0;
            last_err  <= 1'b0;
        end else begin
            start_r <= 1'b0;
            we_r    <= 1'b0;
            if (state != S_RST)
                rst_cnt <= 5'd0;

            if (rx_valid || state == S_IDLE)
                idle_cnt <= 32'd0;
            else if (!rx_stalled)
                idle_cnt <= idle_cnt + 32'd1;

            case (state)

            // ═════════════════════════════════════════════════════
            S_IDLE: begin
                pend     <= 1'b0;
                need_rst <= 1'b0;
                abort    <= 1'b0;
                if (rx_valid) begin
                    cmd       <= rx_data;
                    arg_cnt   <= 2'd0;
                    status    <= ST_OK;
                    resp_kind <= RK_NONE;
                    resp_len  <= 11'd0;
                    state     <= (nargs(rx_data) == 2'd0) ? S_EXEC : S_ARGS;
                end
            end

            // ═════════════════════════════════════════════════════
            S_ARGS: begin
                if (rx_valid) begin
                    if (arg_cnt == 2'd0) arg0 <= rx_data;
                    else                 arg1 <= rx_data;
                    arg_cnt <= arg_cnt + 2'd1;
                    if (arg_cnt + 2'd1 == nargs(cmd))
                        state <= S_EXEC;
                end else if (rx_stalled) begin
                    state <= S_IDLE;
                end
            end

            // ═════════════════════════════════════════════════════
            // EXEC — validate and dispatch
            // ═════════════════════════════════════════════════════
            S_EXEC: begin
                rx_cnt <= 11'd0;
                cnt    <= 11'd0;
                cycles <= 32'd0;
                state  <= S_TX_STATUS;   // default: status-only response

                case (cmd)
                CMD_PING: begin
                    resp_kind <= RK_PING;
                    resp_len  <= 11'd4;
                end

                CMD_RESET: begin
                    state <= S_RST;
                end

                CMD_KEYGEN, CMD_ENCAPS: begin
                    sel   <= (cmd == CMD_KEYGEN) ? 2'd0 : 2'd1;
                    total <= (cmd == CMD_KEYGEN) ? LEN_KEYGEN : LEN_ENCAPS;
                    if (!CAPS[(cmd == CMD_KEYGEN) ? 0 : 1]) begin
                        status <= ST_NO_CORE;
                        state  <= S_DRAIN;
                    end else if ((cmd == CMD_KEYGEN) ? kg_busy : en_busy) begin
                        status <= ST_BUSY;
                        state  <= S_DRAIN;
                    end else begin
                        start_r <= 1'b1;
                        state   <= S_FEED;
                    end
                end

                CMD_DECRYPT: begin
                    sel <= 2'd2;
                    if (!CAPS[2])
                        status <= ST_NO_CORE;
                    else if (de_busy)
                        status <= ST_BUSY;
                    else begin
                        start_r <= 1'b1;
                        state   <= S_RUN;
                    end
                end

                CMD_READ_POLY, CMD_WRITE_POLY: begin
                    sel       <= arg0[1:0];
                    host_slot <= arg1[4:0];
                    total     <= LEN_POLY;
                    if (!arg_core_ok)
                        status <= ST_NO_CORE;
                    else if (arg1 >= NUM_SLOTS)
                        status <= ST_BAD_ARG;
                    else if ((arg0[1:0] == 2'd0) ? kg_busy :
                             (arg0[1:0] == 2'd1) ? en_busy : de_busy)
                        status <= ST_BUSY;

                    if (cmd == CMD_WRITE_POLY)
                        state <= S_WPOLY;       // status checked there
                    else if (arg_core_ok && arg1 < NUM_SLOTS) begin
                        resp_kind <= RK_POLY;
                        resp_len  <= LEN_POLY;
                    end
                end

                CMD_READ_SECRET: begin
                    sel <= arg0[1:0];
                    if (!arg_core_ok)
                        status <= ST_NO_CORE;
                    else if (arg0 == 8'd2)
                        status <= ST_BAD_ARG;   // decaps core has no secret
                    else begin
                        resp_kind <= RK_SECRET;
                        resp_len  <= 11'd32;
                    end
                end

                default: status <= ST_UNKNOWN_CMD;
                endcase
            end

            // ═════════════════════════════════════════════════════
            // FEED — forward payload bytes into the selected core
            // ═════════════════════════════════════════════════════
            S_FEED: begin
                if (rx_valid) begin
                    rx_cnt <= rx_cnt + 11'd1;
                    if (pend && !consume) begin
                        status   <= ST_OVERFLOW;
                        need_rst <= 1'b1;
                        pend     <= 1'b0;
                        state    <= (rx_cnt + 11'd1 == total) ? S_RST : S_DRAIN;
                    end else begin
                        pend      <= 1'b1;
                        pend_data <= rx_data;
                    end
                end else if (consume) begin
                    pend <= 1'b0;
                end

                if (consume) begin
                    cnt <= cnt + 11'd1;
                    if (cnt + 11'd1 == total && status == ST_OK)
                        state <= S_RUN;
                end

                // Host went quiet mid-payload → drop silently.
                // All bytes in but core never took the last one → timeout.
                if (rx_stalled) begin
                    if (rx_cnt != total) abort  <= 1'b1;
                    else                 status <= ST_TIMEOUT;
                    state <= S_RST;
                end
            end

            // ═════════════════════════════════════════════════════
            // RUN — wait for the core's done pulse
            // ═════════════════════════════════════════════════════
            S_RUN: begin
                cycles <= cycles + 32'd1;
                if (sel_done) begin
                    cycles    <= cycles + 32'd1;
                    resp_kind <= RK_CYCLES;
                    resp_len  <= 11'd4;
                    state     <= S_TX_STATUS;
                end else if (cycles >= RUN_TIMEOUT) begin
                    status <= ST_TIMEOUT;
                    state  <= S_RST;
                end
            end

            // ═════════════════════════════════════════════════════
            // WPOLY — receive 256 little-endian u16, write each coeff
            // ═════════════════════════════════════════════════════
            S_WPOLY: begin
                if (rx_valid) begin
                    rx_cnt <= rx_cnt + 11'd1;
                    if (!rx_cnt[0]) begin
                        lo_byte <= rx_data;
                    end else begin
                        host_addr <= rx_cnt[8:1];
                        host_din  <= {rx_data[3:0], lo_byte};
                        we_r      <= (status == ST_OK);
                    end
                    if (rx_cnt + 11'd1 == total)
                        state <= S_TX_STATUS;
                end else if (rx_stalled) begin
                    state <= S_IDLE;
                end
            end

            // ═════════════════════════════════════════════════════
            // DRAIN — discard the rest of a rejected payload
            // ═════════════════════════════════════════════════════
            S_DRAIN: begin
                if (rx_valid) begin
                    rx_cnt <= rx_cnt + 11'd1;
                    if (rx_cnt + 11'd1 == total)
                        state <= need_rst ? S_RST : S_TX_STATUS;
                end else if (rx_stalled) begin
                    abort <= 1'b1;
                    state <= S_RST;
                end
            end

            // ═════════════════════════════════════════════════════
            // RST — hold the cores in reset for 16 cycles
            // ═════════════════════════════════════════════════════
            S_RST: begin
                rst_r   <= 1'b1;
                pend    <= 1'b0;
                rst_cnt <= rst_cnt + 5'd1;
                if (rst_cnt[4]) begin
                    rst_r <= 1'b0;
                    state <= abort ? S_IDLE : S_TX_STATUS;
                end
            end

            // ═════════════════════════════════════════════════════
            // TX — status byte, then resp_len payload bytes
            // ═════════════════════════════════════════════════════
            S_TX_STATUS: begin
                if (tx_ready) begin
                    last_err <= (status != ST_OK);
                    p_idx    <= 11'd0;
                    state    <= (status != ST_OK || resp_len == 11'd0)
                              ? S_IDLE : S_TX_SETUP;
                end
            end

            S_TX_SETUP: begin
                host_addr  <= p_idx[8:1];
                secret_idx <= p_idx[4:0];
                state      <= S_TX_WAIT1;
            end

            S_TX_WAIT1: state <= S_TX_WAIT2;    // address registered
            S_TX_WAIT2: state <= S_TX_LATCH;    // RAM output registered

            S_TX_LATCH: begin
                tx_byte <= payload_byte;
                state   <= S_TX_SEND;
            end

            S_TX_SEND: begin
                if (tx_ready) begin
                    p_idx <= p_idx + 11'd1;
                    state <= (p_idx + 11'd1 == resp_len) ? S_IDLE : S_TX_SETUP;
                end
            end

            default: state <= S_IDLE;
            endcase
        end
    end

endmodule
