"""Evidence run: UART request/response transactions through fpga_top.

Drives evidence_uart_tb (fpga_top + annotation signals) over its serial pins
with the bit-level UART model from tb/fpga_top, and runs six transactions
that together show data going to the FPGA and coming back:

    1 PING         0x01                       → 00 'K' 'Y' version caps
    2 WRITE_POLY   256 known coefficients     → 00
    3 READ_POLY    read them back             → 00 + the same 512 bytes (echo)
    4 KEYGEN       32-byte seed d             → 00 + cycle count (core runs)
    5 READ_SECRET  rho from the keygen core   → 00 + 32 bytes = G(d || 3)[0:32]
    6 BAD_SLOT     READ_POLY slot 25          → 03 (bad argument), link stays in sync

Writes:
    simulate_result/waves/uart_transactions.vcd
    simulate_result/logs/uart_result.txt         per-transaction check, PASS/FAIL
    simulate_result/logs/uart_transactions.txt   request/response bytes + line time windows
    simulate_result/logs/uart_bytes.txt          every byte on uart_rxd / uart_txd with its time
"""

import os
import struct
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb.utils import get_sim_time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'fpga_top'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'ref'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'scripts'))

from test_fpga_top import SimUart, CLK_PERIOD_NS, BIT_NS  # noqa: E402  (bit-level UART model)
from evidence_utils import write_log, ensure_dirs  # noqa: E402
from kyber_acvp import g_hash  # noqa: E402
from kyber_uart import (  # noqa: E402
    CMD_PING, CMD_WRITE_POLY, CMD_READ_POLY, CMD_KEYGEN, CMD_READ_SECRET,
    CORE_KEYGEN, ST_OK, ST_BAD_ARG, STATUS_NAMES,
)

CASE = "uart"
WRITE_SLOT = 5
PATTERN = [0xA00 | i for i in range(256)]     # easy to spot: bytes i, 0x0A, i+1, 0x0A, ...
SEED = bytes(range(32))                      # KEYGEN seed d = 00 01 02 ... 1f
BYTE_NS = 10 * BIT_NS                        # one 8N1 frame


def hex_short(data, limit=12):
    s = data[:limit].hex(' ')
    return s + (f" ... ({len(data)} B)" if len(data) > limit else f"  ({len(data)} B)")


@cocotb.test()
async def test_uart_evidence(dut):
    """Six UART transactions: send data to the FPGA and check what comes back."""
    ensure_dirs()
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    uart = SimUart(dut)
    dut.txn_id.value = 0
    dut.rst_btn.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_btn.value = 0
    await ClockCycles(dut.clk, 300)              # power-on reset stretch

    # ─── Byte monitors (times = clock edge where the byte event happens) ──
    byte_log = []

    async def monitor():
        while True:
            await RisingEdge(dut.clk)
            # Values read here are from the cycle that just ended, so the
            # pulse started one clock earlier — that is the time the VCD shows.
            t = get_sim_time(unit='ns') - CLK_PERIOD_NS
            if int(dut.rx_byte_valid.value):
                byte_log.append((t, "RX", int(dut.rx_byte.value), int(dut.txn_id.value)))
            if int(dut.tx_byte_start.value):
                byte_log.append((t, "TX", int(dut.dut.tx_data.value), int(dut.txn_id.value)))
    cocotb.start_soon(monitor())

    txns = []

    async def transact(txn_id, name, request, resp_len):
        """Send one request, collect resp_len response bytes."""
        await ClockCycles(dut.clk, 20)
        dut.txn_id.value = txn_id
        t_req = get_sim_time(unit='ns')
        await uart._send(request)
        t_req_end = get_sim_time(unit='ns')
        resp = await uart._recv(resp_len)
        t_resp_end = get_sim_time(unit='ns')
        await ClockCycles(dut.clk, 20)
        dut.txn_id.value = 0
        rec = {"id": txn_id, "name": name, "req": bytes(request), "resp": resp,
               "t_req": t_req, "t_req_end": t_req_end, "t_resp_end": t_resp_end}
        txns.append(rec)
        return rec

    # 1 PING
    r = await transact(1, "PING", [CMD_PING], 5)
    r["ok"] = r["resp"][:3] == bytes([ST_OK]) + b"KY"
    r["check"] = (f"status={r['resp'][0]:02x}, signature '{r['resp'][1:3].decode(errors='replace')}', "
                  f"version=0x{r['resp'][3]:02x}, caps=0b{r['resp'][4]:03b}")

    # 2 WRITE_POLY
    payload = struct.pack("<256H", *PATTERN)
    r = await transact(2, "WRITE_POLY", bytes([CMD_WRITE_POLY, CORE_KEYGEN, WRITE_SLOT]) + payload, 1)
    r["ok"] = r["resp"] == bytes([ST_OK])
    r["check"] = f"256 coefficients 0xA00..0xAFF written to keygen core slot {WRITE_SLOT}, status=00"

    # 3 READ_POLY (echo of what was written)
    r = await transact(3, "READ_POLY", [CMD_READ_POLY, CORE_KEYGEN, WRITE_SLOT], 513)
    echoed = list(struct.unpack("<256H", r["resp"][1:])) if len(r["resp"]) == 513 else []
    r["ok"] = r["resp"][:1] == bytes([ST_OK]) and echoed == PATTERN
    r["check"] = (f"returned 512 bytes == written 512 bytes: {echoed == PATTERN} "
                  f"(first coefficients {', '.join(hex(c) for c in echoed[:4])})")

    # 4 KEYGEN (the UART drives the crypto core)
    r = await transact(4, "KEYGEN", bytes([CMD_KEYGEN]) + SEED, 5)
    cycles = struct.unpack("<I", r["resp"][1:5])[0] if len(r["resp"]) == 5 else None
    r["ok"] = r["resp"][:1] == bytes([ST_OK])
    r["check"] = f"status=00, keygen core finished in {cycles} cycles (reported by the FPGA)"

    # 5 READ_SECRET → rho, checked against Python G(d || k)
    r = await transact(5, "READ_SECRET", [CMD_READ_SECRET, CORE_KEYGEN], 33)
    rho_ref, _ = g_hash(SEED + bytes([3]))
    r["ok"] = r["resp"] == bytes([ST_OK]) + rho_ref
    r["check"] = f"rho == SHA3-512(d || 3)[0:32] from Python: {r['resp'][1:] == rho_ref}"

    # 6 BAD_SLOT → error status, then the link must still work
    r = await transact(6, "BAD_SLOT", [CMD_READ_POLY, CORE_KEYGEN, 25], 1)
    r["ok"] = r["resp"] == bytes([ST_BAD_ARG])
    r["check"] = f"status=0x{r['resp'][0]:02x} ({STATUS_NAMES.get(r['resp'][0], '?')}), no payload"

    passed = all(t["ok"] for t in txns)

    # ─── Logs ────────────────────────────────────────────────────
    lines = [
        "UART request/response evidence — fpga_top over its serial pins",
        "DUT: fpga_top (tb/evidence_uart/evidence_uart_tb.v), 8N1, 8 clocks per bit "
        f"(1 bit = {BIT_NS} ns, 1 byte frame = {BYTE_NS} ns)",
        "VCD: simulate_result/waves/uart_transactions.vcd   (clock period 10 ns)",
        "",
        f"RESULT: {'PASS' if passed else 'FAIL'}   ({sum(t['ok'] for t in txns)}/{len(txns)} transactions ok)",
        "",
    ]
    for t in txns:
        lines.append(f"  [{'PASS' if t['ok'] else 'FAIL'}] {t['id']} {t['name']:<12} {t['check']}")
    lines += ["", f"KEYGEN seed d : {SEED.hex()}", f"rho (Python)  : {rho_ref.hex()}",
              f"rho (FPGA)    : {txns[4]['resp'][1:].hex()}"]
    write_log(f"{CASE}_result", lines)

    lines = [
        "UART transactions (signal txn_name in the VCD)",
        "request  = bytes the host sends on uart_rxd (into the FPGA)",
        "response = bytes the FPGA sends back on uart_txd",
        "",
    ]
    for t in txns:
        rx = [b for b in byte_log if b[1] == "RX" and b[3] == t["id"]]
        tx = [b for b in byte_log if b[1] == "TX" and b[3] == t["id"]]
        lines += [
            f"== {t['id']} {t['name']}   [{'PASS' if t['ok'] else 'FAIL'}]",
            f"   request  : {hex_short(t['req'])}",
            f"   response : {hex_short(t['resp'])}",
            f"   uart_rxd : {t['t_req']:.0f} .. {t['t_req_end']:.0f} ns   "
            f"(last byte received by FPGA at {rx[-1][0]:.0f} ns)" if rx else "   uart_rxd : -",
            f"   uart_txd : first byte starts at {tx[0][0]:.0f} ns, last at {tx[-1][0]:.0f} ns, "
            f"host done reading at {t['t_resp_end']:.0f} ns" if tx else "   uart_txd : -",
            f"   check    : {t['check']}",
            "",
        ]
    write_log(f"{CASE}_transactions", lines)

    lines = [
        "Every byte on the serial lines (RX = received by the FPGA on uart_rxd,",
        "TX = sent by the FPGA on uart_txd). Time = where the 1-cycle pulse",
        "rx_byte_valid / tx_byte_start goes high in the VCD; for TX the start bit",
        "appears on uart_txd one clock later. char = printable ASCII or '.'.",
        "",
        f"{'time(ns)':>10}  dir  byte  char  transaction",
        "-" * 50,
    ]
    names = {t["id"]: t["name"] for t in txns}
    for t_ns, direction, byte, tid in byte_log:
        ch = chr(byte) if 0x20 <= byte < 0x7F else "."
        lines.append(f"{t_ns:>10.0f}  {direction}   0x{byte:02x}   {ch}    {tid} {names.get(tid, '-')}")
    write_log(f"{CASE}_bytes", lines)

    dut._log.info(f"UART evidence: {'PASS' if passed else 'FAIL'}; logs in simulate_result/logs/{CASE}_*.txt")
    for t in txns:
        assert t["ok"], f"transaction {t['id']} {t['name']} failed: {t['check']}"
