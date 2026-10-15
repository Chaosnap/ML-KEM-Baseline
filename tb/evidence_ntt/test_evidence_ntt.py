"""Evidence run: 7-layer NTT and INTT on ntt_engine, annotated VCD + logs.

Drives evidence_ntt_tb (ntt_engine + annotation signals) with a fixed-seed
random polynomial:
    1. forward NTT, result checked against ref/kyber_math.ntt_forward
    2. inverse NTT on the result, checked to give back the input polynomial

Writes:
    simulate_result/waves/ntt_roundtrip.vcd
    simulate_result/logs/ntt_roundtrip_result.txt   input/outputs, PASS/FAIL, cycle counts
    simulate_result/logs/ntt_roundtrip_layers.txt   per-layer time ranges + butterfly counts
"""

import os
import random
import sys

import cocotb
from cocotb.triggers import RisingEdge
from cocotb.utils import get_sim_time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'ntt_engine'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'ref'))

import test_ntt_engine as tn  # noqa: E402  (reuses init/load/read/run helpers)
from evidence_utils import SegmentRecorder, ascii_value, write_log, ensure_dirs  # noqa: E402
from kyber_math import KYBER_Q, ntt_forward  # noqa: E402

CASE = "ntt_roundtrip"
SEED = 2026


def poly_rows(coeffs, per_row=16):
    return [f"    [{i:3d}] " + " ".join(f"{c:4d}" for c in coeffs[i:i + per_row])
            for i in range(0, len(coeffs), per_row)]


@cocotb.test()
async def test_ntt_evidence(dut):
    """Forward NTT then inverse NTT with per-layer timeline."""
    ensure_dirs()
    rng = random.Random(SEED)
    poly = [rng.randrange(KYBER_Q) for _ in range(256)]
    await tn.init(dut)
    await tn.load_poly(dut, poly)

    def layer_key():
        state = int(dut.state_id.value)
        if 1 <= state <= 4:
            return (ascii_value(dut.mode_name), int(dut.layer_no.value),
                    int(dut.bf_len.value), int(dut.bf_groups.value))
        if 5 <= state <= 7:
            return (ascii_value(dut.mode_name), "SCALE", 0, 0)
        return None

    layers = SegmentRecorder(dut, layer_key, lambda: int(dut.cycle_count.value),
                             end_fn=lambda: int(dut.bf_count.value)).start()

    runs = {}
    for mode, name in ((tn.MODE_NTT, "NTT"), (tn.MODE_INTT, "INTT")):
        await RisingEdge(dut.clk)
        t_start = get_sim_time(unit='ns') + tn.CLK_PERIOD_NS       # edge that samples start
        cycles = await tn.run_transform(dut, mode)
        t_done = get_sim_time(unit='ns') - tn.CLK_PERIOD_NS / 2   # edge where done rises
        runs[name] = (t_start, t_done, cycles, await tn.read_poly(dut))
    layers.stop()

    ntt_expected = ntt_forward(poly)
    fwd_ok = runs["NTT"][3] == ntt_expected
    inv_ok = runs["INTT"][3] == poly
    passed = fwd_ok and inv_ok

    # ─── Result log ──────────────────────────────────────────────
    lines = [
        "ntt_engine — forward NTT + inverse NTT round trip",
        f"DUT: ntt_engine (tb/evidence_ntt/evidence_ntt_tb.v), input: random.Random({SEED}) polynomial",
        f"VCD: simulate_result/waves/{CASE}.vcd   (clock period 10 ns)",
        "",
        f"RESULT: {'PASS' if passed else 'FAIL'}   "
        f"(NTT vs ref/kyber_math.ntt_forward: {'match' if fwd_ok else 'MISMATCH'}; "
        f"INTT(NTT(a)) == a: {'yes' if inv_ok else 'NO'})",
    ]
    for name in ("NTT", "INTT"):
        t0, t1, cyc, _ = runs[name]
        lines.append(f"{name:<4}: start sampled at t = {t0:.0f} ns, done at t = {t1:.0f} ns, "
                     f"{cyc} cycles")
    lines += ["", "Input polynomial a (256 coefficients mod 3329):", *poly_rows(poly),
              "", "Hardware NTT(a):", *poly_rows(runs["NTT"][3]),
              "", "Reference ntt_forward(a):", *poly_rows(ntt_expected),
              "", "Hardware INTT(NTT(a)):", *poly_rows(runs["INTT"][3])]
    write_log(f"{CASE}_result", lines)

    # ─── Layer timeline ──────────────────────────────────────────
    lines = [
        "ntt_engine layer timeline (signals layer_name / state_name / bf_count in the VCD)",
        "Each layer: 1 LAYER_INIT + 1 BF_PRIME + 127 BF_OVERLAP + 1 BF_FLUSH = 130 cycles,",
        "128 butterflies (one per cycle, ping-pong between RAM A and RAM B).",
        "INTT adds a scaling pass (x 128^-1 mod q, two coefficients per cycle).",
    ]
    for name in ("NTT", "INTT"):
        t0, t1, cyc, _ = runs[name]
        lines += ["", f"== {name}: {cyc} cycles  (t = {t0:.0f} .. {t1:.0f} ns)",
                  f"   {'layer':<6} {'len':>4} {'groups':>6} {'start(ns)':>10} {'end(ns)':>10} "
                  f"{'cycles':>6} {'butterflies':>11}",
                  "   " + "-" * 60]
        for seg in layers.segments:
            mode, layer, length, groups = seg['key']
            if mode != name:
                continue
            if layer == "SCALE":
                lines.append(f"   {'SCALE':<6} {'':>4} {'':>6} {seg['start_ns']:>10.0f} "
                             f"{seg['end_ns']:>10.0f} {seg['cycles']:>6} {'(256 coeffs)':>11}")
            else:
                lines.append(f"   L{layer:<5} {length:>4} {groups:>6} {seg['start_ns']:>10.0f} "
                             f"{seg['end_ns']:>10.0f} {seg['cycles']:>6} {seg['end_value']:>11}")
    write_log(f"{CASE}_layers", lines)

    dut._log.info(f"NTT {runs['NTT'][2]} cycles, INTT {runs['INTT'][2]} cycles: "
                  f"{'PASS' if passed else 'FAIL'}; logs in simulate_result/logs/{CASE}_*.txt")
    assert fwd_ok, "forward NTT mismatch vs reference"
    assert inv_ok, "INTT(NTT(a)) != a"
