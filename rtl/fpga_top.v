// fpga_top — Board-level top: ML-KEM-768 cores behind a UART command port
//
// Connects uart_rx/uart_tx → uart_host (protocol FSM) → up to three cores:
//   core 0: auto_keygen_top   (ENABLE_KEYGEN)
//   core 1: auto_encaps_top   (ENABLE_ENCAPS)
//   core 2: decaps_top        (ENABLE_DECAPS, K-PKE.Decrypt)
// See uart_host.v for the wire protocol and scripts/ for the host-side tools.
//
// Each core carries its own kyber_top (≈25 RAMB18). An XC7A35T has 50 RAMB18,
// so enable at most two cores there; larger parts (XC7A100T) fit all three.
//
// Clocking: with MMCM_DIV > 0 the 100 MHz board clock goes through an MMCM
// (VCO 1000 MHz) and the design runs at 1000/MMCM_DIV MHz; CLK_FREQ must then
// be set to that frequency. The cores are not pipelined (basemul_unit chains
// 5 DSP48s, ~41 ns on Artix-7 -1), so they need a ≤ 20 MHz clock on hardware.
// MMCM_DIV = 0 uses the input clock directly (simulation).
//
// LEDs: [0] heartbeat, [1] a core is busy, [2] request in progress,
//       [3] last response was an error.

module fpga_top #(
    parameter CLK_FREQ       = 100_000_000, // core clock frequency (Hz)
    parameter MMCM_DIV       = 0,           // 0: no MMCM; else core clk = 1000 MHz / MMCM_DIV
    parameter BAUD           = 115_200,
    parameter RST_ACTIVE_LOW = 0,           // 1 for boards with an active-low reset button
    parameter ENABLE_KEYGEN  = 1,
    parameter ENABLE_ENCAPS  = 1,
    parameter ENABLE_DECAPS  = 0
) (
    input  wire       clk,                  // 100 MHz board clock
    input  wire       rst_btn,
    input  wire       uart_rxd,             // from USB-UART bridge
    output wire       uart_txd,             // to USB-UART bridge
    output wire [3:0] led
);

    localparam CLKS_PER_BIT = CLK_FREQ / BAUD;
    localparam [2:0] CAPS   = {ENABLE_DECAPS != 0, ENABLE_ENCAPS != 0, ENABLE_KEYGEN != 0};

    // ─── Clock ───────────────────────────────────────────────────
    wire sys_clk;
    wire clk_locked;

    generate if (MMCM_DIV > 0) begin : g_mmcm
        wire clk_fb, clk_mmcm;

        MMCME2_BASE #(
            .CLKIN1_PERIOD   (10.0),        // 100 MHz in
            .DIVCLK_DIVIDE   (1),
            .CLKFBOUT_MULT_F (10.0),        // VCO 1000 MHz
            .CLKOUT1_DIVIDE  (MMCM_DIV)
        ) u_mmcm (
            .CLKIN1   (clk),
            .CLKFBIN  (clk_fb),
            .CLKFBOUT (clk_fb),
            .CLKOUT1  (clk_mmcm),
            .LOCKED   (clk_locked),
            .RST      (1'b0),
            .PWRDWN   (1'b0),
            .CLKFBOUTB(), .CLKOUT0(), .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2(),
            .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6()
        );

        BUFG u_bufg (.I(clk_mmcm), .O(sys_clk));
    end else begin : g_no_mmcm
        assign sys_clk    = clk;
        assign clk_locked = 1'b1;
    end endgenerate

    // ─── Reset: synchronize button, stretch at power-on ──────────
    wire      rst_req = (RST_ACTIVE_LOW ? ~rst_btn : rst_btn) | ~clk_locked;
    reg [1:0] rst_sync = 2'b11;
    reg [7:0] por_cnt  = 8'd0;
    reg       rst_n    = 1'b0;

    always @(posedge sys_clk) begin
        rst_sync <= {rst_sync[0], rst_req};
        if (rst_sync[1]) begin
            por_cnt <= 8'd0;
            rst_n   <= 1'b0;
        end else if (por_cnt != 8'hFF) begin
            por_cnt <= por_cnt + 8'd1;
            rst_n   <= 1'b0;
        end else begin
            rst_n   <= 1'b1;
        end
    end

    // ─── UART ────────────────────────────────────────────────────
    wire [7:0] rx_data;
    wire       rx_valid;
    wire [7:0] tx_data;
    wire       tx_valid;
    wire       tx_ready;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (
        .clk        (sys_clk),
        .rst_n      (rst_n),
        .rxd        (uart_rxd),
        .data       (rx_data),
        .data_valid (rx_valid),
        .frame_err  ()
    );

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (
        .clk   (sys_clk),
        .rst_n (rst_n),
        .valid (tx_valid),
        .data  (tx_data),
        .ready (tx_ready),
        .txd   (uart_txd)
    );

    // ─── Host protocol FSM ───────────────────────────────────────
    wire        core_rst_n_req;
    wire        kg_start, kg_done, kg_busy, kg_in_valid, kg_in_ready, kg_we;
    wire [11:0] kg_dout;
    wire [7:0]  kg_secret;
    wire        en_start, en_done, en_busy, en_in_valid, en_in_ready, en_we;
    wire [11:0] en_dout;
    wire [7:0]  en_secret;
    wire        de_start, de_done, de_busy, de_we;
    wire [11:0] de_dout;
    wire [7:0]  in_data;
    wire [4:0]  host_slot;
    wire [7:0]  host_addr;
    wire [11:0] host_din;
    wire [4:0]  secret_idx;
    wire        host_active;
    wire        last_err;

    uart_host #(
        .CAPS        (CAPS),
        .RX_TIMEOUT  (CLK_FREQ / 10),      // 100 ms
        .RUN_TIMEOUT (CLK_FREQ / 10)       // cores finish in < 1 ms
    ) u_host (
        .clk         (sys_clk),
        .rst_n       (rst_n),
        .rx_data     (rx_data),
        .rx_valid    (rx_valid),
        .tx_data     (tx_data),
        .tx_valid    (tx_valid),
        .tx_ready    (tx_ready),
        .core_rst_n  (core_rst_n_req),

        .kg_start    (kg_start),
        .kg_done     (kg_done),
        .kg_busy     (kg_busy),
        .kg_in_valid (kg_in_valid),
        .kg_in_ready (kg_in_ready),
        .kg_we       (kg_we),
        .kg_dout     (kg_dout),
        .kg_secret   (kg_secret),

        .en_start    (en_start),
        .en_done     (en_done),
        .en_busy     (en_busy),
        .en_in_valid (en_in_valid),
        .en_in_ready (en_in_ready),
        .en_we       (en_we),
        .en_dout     (en_dout),
        .en_secret   (en_secret),

        .de_start    (de_start),
        .de_done     (de_done),
        .de_busy     (de_busy),
        .de_we       (de_we),
        .de_dout     (de_dout),

        .in_data     (in_data),
        .host_slot   (host_slot),
        .host_addr   (host_addr),
        .host_din    (host_din),
        .secret_idx  (secret_idx),

        .active      (host_active),
        .last_err    (last_err)
    );

    // Core reset: board reset OR soft reset from the host (registered)
    reg core_rst_n = 1'b0;
    always @(posedge sys_clk)
        core_rst_n <= rst_n & core_rst_n_req;

    // ─── Core 0: KeyGen ──────────────────────────────────────────
    generate if (ENABLE_KEYGEN) begin : g_keygen
        auto_keygen_top u_keygen (
            .clk          (sys_clk),
            .rst_n        (core_rst_n),
            .start        (kg_start),
            .done         (kg_done),
            .busy         (kg_busy),
            .seed_valid   (kg_in_valid),
            .seed_data    (in_data),
            .seed_ready   (kg_in_ready),
            .host_we      (kg_we),
            .host_slot    (host_slot),
            .host_addr    (host_addr),
            .host_din     (host_din),
            .host_dout    (kg_dout),
            .rho_byte_idx (secret_idx),
            .rho_byte_out (kg_secret)
        );
    end else begin : g_no_keygen
        assign kg_done     = 1'b0;
        assign kg_busy     = 1'b0;
        assign kg_in_ready = 1'b0;
        assign kg_dout     = 12'd0;
        assign kg_secret   = 8'd0;
    end endgenerate

    // ─── Core 1: Encaps ──────────────────────────────────────────
    generate if (ENABLE_ENCAPS) begin : g_encaps
        auto_encaps_top u_encaps (
            .clk        (sys_clk),
            .rst_n      (core_rst_n),
            .start      (en_start),
            .done       (en_done),
            .busy       (en_busy),
            .din_valid  (en_in_valid),
            .din_data   (in_data),
            .din_ready  (en_in_ready),
            .host_we    (en_we),
            .host_slot  (host_slot),
            .host_addr  (host_addr),
            .host_din   (host_din),
            .host_dout  (en_dout),
            .k_byte_idx (secret_idx),
            .k_byte_out (en_secret)
        );
    end else begin : g_no_encaps
        assign en_done     = 1'b0;
        assign en_busy     = 1'b0;
        assign en_in_ready = 1'b0;
        assign en_dout     = 12'd0;
        assign en_secret   = 8'd0;
    end endgenerate

    // ─── Core 2: Decrypt ─────────────────────────────────────────
    generate if (ENABLE_DECAPS) begin : g_decaps
        decaps_top u_decaps (
            .clk           (sys_clk),
            .rst_n         (core_rst_n),
            .host_we       (de_we),
            .host_slot     (host_slot),
            .host_addr     (host_addr),
            .host_din      (host_din),
            .host_dout     (de_dout),
            .decrypt_start (de_start),
            .decrypt_done  (de_done),
            .decrypt_busy  (de_busy)
        );
    end else begin : g_no_decaps
        assign de_done = 1'b0;
        assign de_busy = 1'b0;
        assign de_dout = 12'd0;
    end endgenerate

    // ─── LEDs ────────────────────────────────────────────────────
    // ~1-2 Hz blink at any CLK_FREQ in 16-100 MHz
    localparam HB_BIT = (CLK_FREQ > 50_000_000) ? 26 : 24;
    reg [26:0] hb_cnt = 27'd0;
    always @(posedge sys_clk)
        hb_cnt <= hb_cnt + 27'd1;

    assign led[0] = hb_cnt[HB_BIT];
    assign led[1] = kg_busy | en_busy | de_busy;
    assign led[2] = host_active;
    assign led[3] = last_err;

endmodule
