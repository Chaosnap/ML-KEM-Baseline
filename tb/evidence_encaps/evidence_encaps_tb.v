// evidence_encaps_tb — Annotated waveform wrapper around auto_encaps_top
//
// Simulation only. Instantiates the unmodified auto_encaps_top and adds
// human-readable annotation signals (ASCII strings: set the viewer's display
// format to ASCII) so a VCD shows *what the hardware is doing* at each cycle:
//
//   cycle_count        cycles since the last start pulse (0 before start)
//   phase_id/_name     auto_encaps_ctrl top-level phase (1..10)
//   expand_entry       matrix entry A[i][j] being sampled in EXPAND_A (0..8)
//   poly_step          POLY_OPS micro-op step 0..85
//   poly_stage_name    algorithm stage of that step, e.g. "u[1]=At.r+e1"
//   keccak_name        Keccak sponge state (IDLE/ABSORB/PAD/PERMUTE/SQUEEZE)
//   keccak_round       Keccak-f round 0..23 while permuting
//   kt_op_name         kyber_top micro-op currently executing ("-" when idle)
//   ntt_state_name     ntt_engine FSM state inside kyber_top
//   ntt_layer_name     NTT/INTT layer label, e.g. "NTT L3 len=32"
//   m_bytes, rho_bytes, h_bytes, k_bytes, r_bytes
//                      the controller's 32-byte registers (m, rho, H(ek), K, r)
//                      byte-reversed so a hex display reads byte 0 first,
//                      i.e. in the same order as the logs / NIST vectors
//
// The VCD path comes from the +vcd=<path> plusarg (see Makefile). Dumped
// scopes: this module (ports + annotations), u_ctrl, u_keccak, u_kt, u_kt.u_ntt.

module evidence_encaps_tb (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    output wire        done,
    output wire        busy,
    input  wire        din_valid,
    input  wire [7:0]  din_data,
    output wire        din_ready,
    input  wire        host_we,
    input  wire [4:0]  host_slot,
    input  wire [7:0]  host_addr,
    input  wire [11:0] host_din,
    output wire [11:0] host_dout,
    input  wire [4:0]  k_byte_idx,
    output wire [7:0]  k_byte_out
);

    auto_encaps_top dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .start      (start),
        .done       (done),
        .busy       (busy),
        .din_valid  (din_valid),
        .din_data   (din_data),
        .din_ready  (din_ready),
        .host_we    (host_we),
        .host_slot  (host_slot),
        .host_addr  (host_addr),
        .host_din   (host_din),
        .host_dout  (host_dout),
        .k_byte_idx (k_byte_idx),
        .k_byte_out (k_byte_out)
    );

    // ─── Cycle counter ───────────────────────────────────────────
    reg [31:0] cycle_count = 32'd0;
    always @(posedge clk)
        if (start)     cycle_count <= 32'd0;
        else if (busy) cycle_count <= cycle_count + 32'd1;

    // ─── Encaps phase ────────────────────────────────────────────
    wire [3:0] phase_id     = dut.u_ctrl.phase;
    wire [3:0] expand_entry = dut.u_ctrl.expand_idx;
    reg  [8*10-1:0] phase_name;
    always @(*) begin
        case (phase_id)
            4'd0:    phase_name = "IDLE";
            4'd1:    phase_name = "INPUT_M";
            4'd2:    phase_name = "INPUT_EK";
            4'd3:    phase_name = "H_SQUEEZE";
            4'd4:    phase_name = "G_ABSORB";
            4'd5:    phase_name = "G_SQUEEZE";
            4'd6:    phase_name = "EXPAND_A";
            4'd7:    phase_name = "LOAD_M";
            4'd8:    phase_name = "PRF_CBD";
            4'd9:    phase_name = "POLY_OPS";
            4'd10:   phase_name = "DONE";
            default: phase_name = "?";
        endcase
    end

    // ─── POLY_OPS stage (steps from auto_encaps_ctrl's decode table) ─
    wire [6:0] poly_step = dut.u_ctrl.po_step;
    reg  [8*14-1:0] poly_stage_name;
    always @(*) begin
        if (phase_id != 4'd9)      poly_stage_name = "-";
        else if (poly_step <= 8)   poly_stage_name = "NTT(r)";
        else if (poly_step <= 26)  poly_stage_name = "u[0]=At.r+e1";
        else if (poly_step <= 44)  poly_stage_name = "u[1]=At.r+e1";
        else if (poly_step <= 62)  poly_stage_name = "u[2]=At.r+e1";
        else if (poly_step <= 81)  poly_stage_name = "v=tt.r+e2+mu";
        else                       poly_stage_name = "COMPRESS";
    end

    // ─── Keccak sponge ───────────────────────────────────────────
    wire [4:0] keccak_round = dut.u_keccak.round_ctr;
    reg  [8*7-1:0] keccak_name;
    always @(*) begin
        case (dut.u_keccak.fsm_state)
            3'd0:    keccak_name = "IDLE";
            3'd1:    keccak_name = "ABSORB";
            3'd2:    keccak_name = "PAD";
            3'd3:    keccak_name = "PERMUTE";
            3'd4:    keccak_name = "SQUEEZE";
            default: keccak_name = "?";
        endcase
    end

    // ─── kyber_top micro-op ──────────────────────────────────────
    reg [8*13-1:0] kt_op_name;
    always @(*) begin
        if (dut.u_kt.state == 3'd0)
            kt_op_name = "-";
        else case (dut.u_kt.op_reg)
            4'd1:    kt_op_name = "COPY_TO_NTT";
            4'd2:    kt_op_name = "COPY_FROM_NTT";
            4'd3:    kt_op_name = dut.u_kt.param_reg[0] ? "RUN_INTT" : "RUN_NTT";
            4'd4:    kt_op_name = "COPY_TO_BM_A";
            4'd5:    kt_op_name = "COPY_TO_BM_B";
            4'd6:    kt_op_name = "COPY_FROM_BM";
            4'd7:    kt_op_name = "RUN_BASEMUL";
            4'd8:    kt_op_name = "POLY_ADD";
            4'd9:    kt_op_name = "POLY_SUB";
            4'd10:   kt_op_name = "COMPRESS";
            4'd11:   kt_op_name = "DECOMPRESS";
            4'd12:   kt_op_name = "CBD_SAMPLE";
            default: kt_op_name = "NOP";
        endcase
    end

    // ─── NTT engine inside kyber_top ─────────────────────────────
    reg [8*11-1:0] ntt_state_name;
    always @(*) begin
        case (dut.u_kt.u_ntt.state)
            4'd0:    ntt_state_name = "IDLE";
            4'd1:    ntt_state_name = "LAYER_INIT";
            4'd2:    ntt_state_name = "BF_PRIME";
            4'd3:    ntt_state_name = "BF_OVERLAP";
            4'd4:    ntt_state_name = "BF_FLUSH";
            4'd5:    ntt_state_name = "SCALE_INIT";
            4'd6:    ntt_state_name = "SCALE_READ";
            4'd7:    ntt_state_name = "SCALE_WRITE";
            4'd8:    ntt_state_name = "DONE";
            default: ntt_state_name = "?";
        endcase
    end

    wire       ntt_in_layer = (dut.u_kt.u_ntt.state >= 4'd1) && (dut.u_kt.u_ntt.state <= 4'd4);
    wire       ntt_inverse  = dut.u_kt.u_ntt.mode_reg;
    wire [2:0] ntt_layer    = dut.u_kt.u_ntt.layer;
    reg  [8*15-1:0] ntt_layer_name;
    always @(*) begin
        if (!ntt_in_layer)
            ntt_layer_name = (dut.u_kt.u_ntt.state >= 4'd5 && dut.u_kt.u_ntt.state <= 4'd7)
                           ? "INTT SCALE" : "-";
        else if (!ntt_inverse)
            ntt_layer_name = layer_label("NTT ",  ntt_layer, 8'd128 >> ntt_layer);
        else
            ntt_layer_name = layer_label("INTT", ntt_layer, 8'd2 << ntt_layer);
    end

    // "NTT  L3 len= 32" style label; layer shown 1-based
    function [8*15-1:0] layer_label;
        input [8*4-1:0] kind;
        input [2:0]     layer;
        input [7:0]     len;
        reg   [7:0]     d_layer, d100, d10, d1;
        begin
            d_layer = 8'h31 + {5'd0, layer};                          // '1' + layer
            d100    = (len >= 8'd100) ? 8'h30 + len / 8'd100 : 8'h20;  // digit or ' '
            d10     = (len >= 8'd10)  ? 8'h30 + (len / 8'd10) % 8'd10 : 8'h20;
            d1      = 8'h30 + len % 8'd10;
            layer_label = {kind, " L", d_layer, " len=", d100, d10, d1};
        end
    endfunction

    // ─── 32-byte registers in natural byte order ────────────────
    // The controller stores byte i at bits [8i+7:8i]; reversing the bytes
    // makes a hex display read byte 0 first.
    function [255:0] byte_order;
        input [255:0] x;
        integer i;
        begin
            for (i = 0; i < 32; i = i + 1)
                byte_order[(31 - i) * 8 +: 8] = x[i * 8 +: 8];
        end
    endfunction

    wire [255:0] m_bytes   = byte_order(dut.u_ctrl.m_reg);
    wire [255:0] rho_bytes = byte_order(dut.u_ctrl.rho);
    wire [255:0] h_bytes   = byte_order(dut.u_ctrl.h_reg);
    wire [255:0] k_bytes   = byte_order(dut.u_ctrl.k_reg);
    wire [255:0] r_bytes   = byte_order(dut.u_ctrl.r_reg);

    // ─── Waveform dump ───────────────────────────────────────────
    initial begin : vcd_setup
        reg [8*512-1:0] vcd_file;
        if (!$value$plusargs("vcd=%s", vcd_file))
            vcd_file = "evidence_encaps.vcd";
        $dumpfile(vcd_file);
        // Icarus repeats the top scope once per $dumpvars call; the Makefile
        // merges the copies afterwards (tb/vcd_merge_scopes.py).
        $dumpvars(1, evidence_encaps_tb);
        $dumpvars(1, dut.u_ctrl);
        $dumpvars(1, dut.u_keccak);
        $dumpvars(1, dut.u_kt);
        $dumpvars(1, dut.u_kt.u_ntt);
    end

endmodule
