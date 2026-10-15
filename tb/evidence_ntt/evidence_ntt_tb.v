// evidence_ntt_tb — Annotated waveform wrapper around ntt_engine
//
// Simulation only. Instantiates the unmodified ntt_engine and adds
// human-readable annotation signals (ASCII strings: set the viewer's display
// format to ASCII) that expose the 7-layer rhythm of the transform:
//
//   cycle_count      cycles since the last start pulse (0 before start)
//   mode_name        "NTT" (forward) or "INTT" (inverse)
//   state_name       FSM state: IDLE / LAYER_INIT / BF_PRIME / BF_OVERLAP /
//                    BF_FLUSH / SCALE_INIT / SCALE_READ / SCALE_WRITE / DONE
//   layer_no         current layer 1..7 (0 outside the layers)
//   layer_name       e.g. "NTT  L3 len= 32" (butterfly distance = len)
//   bf_len/bf_groups butterfly distance and number of groups in this layer
//   bf_count         butterflies written so far in this layer (ends at 128)
//   pingpong         "A->B" or "B->A": source → destination RAM of this layer
//
// The VCD path comes from the +vcd=<path> plusarg (see Makefile). Dumped
// scopes: this module (ports + annotations) and dut (engine internals).

module evidence_ntt_tb (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        mode,
    output wire        done,
    output wire        busy,
    input  wire        ext_we,
    input  wire [7:0]  ext_addr,
    input  wire [11:0] ext_din,
    output wire [11:0] ext_dout
);

    ntt_engine dut (
        .clk      (clk),
        .rst_n    (rst_n),
        .start    (start),
        .mode     (mode),
        .done     (done),
        .busy     (busy),
        .ext_we   (ext_we),
        .ext_addr (ext_addr),
        .ext_din  (ext_din),
        .ext_dout (ext_dout)
    );

    // ─── Cycle counter ───────────────────────────────────────────
    reg [31:0] cycle_count = 32'd0;
    always @(posedge clk)
        if (start)     cycle_count <= 32'd0;
        else if (busy) cycle_count <= cycle_count + 32'd1;

    // ─── Mode / state names ──────────────────────────────────────
    wire [3:0] state_id = dut.state;
    reg  [8*4-1:0]  mode_name;
    reg  [8*11-1:0] state_name;
    always @(*) begin
        mode_name = dut.mode_reg ? "INTT" : "NTT";
        case (state_id)
            4'd0:    state_name = "IDLE";
            4'd1:    state_name = "LAYER_INIT";
            4'd2:    state_name = "BF_PRIME";
            4'd3:    state_name = "BF_OVERLAP";
            4'd4:    state_name = "BF_FLUSH";
            4'd5:    state_name = "SCALE_INIT";
            4'd6:    state_name = "SCALE_READ";
            4'd7:    state_name = "SCALE_WRITE";
            4'd8:    state_name = "DONE";
            default: state_name = "?";
        endcase
    end

    // ─── Layer ───────────────────────────────────────────────────
    wire       in_layer  = (state_id >= 4'd1) && (state_id <= 4'd4);
    wire [3:0] layer_no  = in_layer ? {1'b0, dut.layer} + 4'd1 : 4'd0;
    wire [7:0] bf_len    = in_layer ? dut.bf_length : 8'd0;
    wire [6:0] bf_groups = in_layer ? dut.bf_groups : 7'd0;
    wire [6:0] group     = dut.group;
    wire [6:0] j_offset  = dut.j_offset;

    reg [8*15-1:0] layer_name;
    always @(*) begin
        if (in_layer)
            layer_name = layer_label(dut.mode_reg ? "INTT" : "NTT ", dut.layer, dut.bf_length);
        else if (state_id >= 4'd5 && state_id <= 4'd7)
            layer_name = "INTT SCALE";
        else
            layer_name = "-";
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

    // Butterflies written in this layer: one per BF_OVERLAP/BF_FLUSH cycle
    reg [7:0] bf_count = 8'd0;
    always @(posedge clk)
        if (state_id == 4'd1)                        bf_count <= 8'd0;
        else if (state_id == 4'd3 || state_id == 4'd4) bf_count <= bf_count + 8'd1;

    // Ping-pong direction: source RAM → destination RAM
    reg [8*4-1:0] pingpong;
    always @(*)
        pingpong = !in_layer ? "-" : (dut.src_sel ? "B->A" : "A->B");

    // ─── Waveform dump ───────────────────────────────────────────
    initial begin : vcd_setup
        reg [8*512-1:0] vcd_file;
        if (!$value$plusargs("vcd=%s", vcd_file))
            vcd_file = "evidence_ntt.vcd";
        $dumpfile(vcd_file);
        // Icarus repeats the top scope once per $dumpvars call; the Makefile
        // merges the copies afterwards (tb/vcd_merge_scopes.py).
        $dumpvars(1, evidence_ntt_tb);
        $dumpvars(1, dut);
    end

endmodule
