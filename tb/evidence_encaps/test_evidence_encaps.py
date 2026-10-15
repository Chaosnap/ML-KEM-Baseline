"""Evidence run: ML-KEM-768 Encaps on NIST ACVP tcId=26, annotated VCD + logs.

Drives evidence_encaps_tb (auto_encaps_top + annotation signals) with the
first ML-KEM-768 ACVP encapsulation vector, checks c and K bit-exactly
against NIST, and writes:

    simulate_result/waves/encaps_tc26.vcd
    simulate_result/logs/encaps_tc26_result.txt       inputs, NIST vs hardware, PASS/FAIL
    simulate_result/logs/encaps_tc26_phases.txt       phase 1..10 time ranges
    simulate_result/logs/encaps_tc26_expand_a.txt     9 SampleNTT entries of A
    simulate_result/logs/encaps_tc26_poly_ops.txt     86 kyber_top micro-ops
"""

import hashlib
import os
import sys

import cocotb
from cocotb.triggers import RisingEdge
from cocotb.utils import get_sim_time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'auto_encaps'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'ref'))

import test_auto_encaps as ta  # noqa: E402  (reuses init/feed/read helpers)
from evidence_utils import (  # noqa: E402
    SegmentRecorder, ascii_value, write_log, hex_block, ensure_dirs,
)
from kyber_acvp import K, DU, DV, byte_encode, byte_decode, g_hash, h_hash  # noqa: E402

TC_ID = 26
CASE = f"encaps_tc{TC_ID}"

PHASES = {
    1: ("INPUT_M",   "接收 32 字节消息 m"),
    2: ("INPUT_EK",  "接收 1184 字节 ek：解析 t̂ 写入 RAM，同时 SHA3-256 吸收 ek"),
    3: ("H_SQUEEZE", "输出 h = H(ek)（32 字节）"),
    4: ("G_ABSORB",  "SHA3-512 吸收 m ‖ h"),
    5: ("G_SQUEEZE", "输出 (K, r) = G(m ‖ h)，K 即共享密钥"),
    6: ("EXPAND_A",  "SHAKE-128 拒绝采样生成矩阵 Â 的 9 个多项式"),
    7: ("LOAD_M",    "μ = Decompress₁(m)：每个比特 → 0 或 1665"),
    8: ("PRF_CBD",   "7 次 SHAKE-256(r ‖ N) + CBD 采样 → y[0..2], e1[0..2], e2"),
    9: ("POLY_OPS",  "86 条微操作：NTT、basemul、INTT、加法、压缩"),
    10: ("DONE",     "完成，done 拉高 1 个周期"),
}

POLY_STAGES = [
    ("NTT(r)",        "ŷ = NTT(y[0..2])"),
    ("u[0]=At.r+e1",  "u[0] = INTT(Σ Â[j][0]·ŷ[j]) + e1[0]"),
    ("u[1]=At.r+e1",  "u[1] = INTT(Σ Â[j][1]·ŷ[j]) + e1[1]"),
    ("u[2]=At.r+e1",  "u[2] = INTT(Σ Â[j][2]·ŷ[j]) + e1[2]"),
    ("v=tt.r+e2+mu",  "v = INTT(Σ t̂[j]·ŷ[j]) + e2 + μ"),
    ("COMPRESS",      "c1 = Compress₁₀(u), c2 = Compress₄(v)"),
]


def load_vector():
    for vec in ta.load_vectors():
        if vec["tcId"] == TC_ID:
            return vec
    raise AssertionError(f"ACVP encaps tcId={TC_ID} not found")


@cocotb.test()
async def test_encaps_evidence(dut):
    """NIST tcId=26 through auto_encaps_top with phase/micro-op timelines."""
    ensure_dirs()
    vec = load_vector()
    await ta.init(dut)

    cycle = lambda: int(dut.cycle_count.value)
    phases = SegmentRecorder(dut, lambda: int(dut.phase_id.value) or None, cycle).start()
    entries = SegmentRecorder(
        dut, lambda: int(dut.expand_entry.value) if int(dut.phase_id.value) == 6 else None,
        cycle).start()
    ops = SegmentRecorder(
        dut, lambda: (ascii_value(dut.poly_stage_name), int(dut.poly_step.value),
                      ascii_value(dut.kt_op_name))
        if int(dut.phase_id.value) == 9 else None, cycle).start()

    await RisingEdge(dut.clk)
    # start is driven after this edge and sampled on the next one;
    # run_auto_encaps returns at the falling edge where done is high.
    t_start = get_sim_time(unit='ns') + ta.CLK_PERIOD_NS
    cycles = await ta.run_auto_encaps(dut, vec["m"], vec["ek"])
    t_done = get_sim_time(unit='ns') - ta.CLK_PERIOD_NS / 2
    await RisingEdge(dut.clk)
    for rec in (phases, entries, ops):
        rec.stop()

    # Read back ciphertext and K, encode as FIPS 203 byte strings
    u = [await ta.read_poly(dut, 16 + i) for i in range(K)]
    v = await ta.read_poly(dut, 19)
    c_hw = b"".join(byte_encode(DU, p) for p in u) + byte_encode(DV, v)
    k_hw = await ta.read_k(dut)
    c_ok = c_hw == vec["expected_c"]
    k_ok = k_hw == vec["expected_k"]
    passed = c_ok and k_ok

    # ─── Result log ──────────────────────────────────────────────
    lines = [
        f"ML-KEM-768 Encaps — NIST ACVP encapsulation tcId={TC_ID}",
        f"DUT: auto_encaps_top (tb/evidence_encaps/evidence_encaps_tb.v)",
        f"VCD: simulate_result/waves/{CASE}.vcd   (clock period 10 ns)",
        "",
        f"RESULT: {'PASS' if passed else 'FAIL'}   "
        f"(c {'match' if c_ok else 'MISMATCH'}, K {'match' if k_ok else 'MISMATCH'})",
        f"start sampled at clock edge t = {t_start:.0f} ns",
        f"done high from clock edge   t = {t_done:.0f} ns",
        f"latency     : {cycles} clock cycles",
        "",
        "Input m (32 B):", *hex_block(vec["m"]),
        f"Input ek (1184 B): SHA3-256(ek) = {hashlib.sha3_256(vec['ek']).hexdigest()}",
        *hex_block(vec["ek"]),
        "",
        "Intermediate values (compare with m_bytes / rho_bytes / h_bytes / k_bytes / r_bytes in the VCD):",
        f"    m            = {vec['m'].hex()}",
        f"    rho          = {vec['ek'][1152:].hex()}   (last 32 bytes of ek)",
        f"    h = H(ek)    = {h_hash(vec['ek']).hex()}   (SHA3-256, Python hashlib)",
        f"    K = G(m||h)  = {g_hash(vec['m'] + h_hash(vec['ek']))[0].hex()}   (SHA3-512 bytes 0..31)",
        f"    r = G(m||h)  = {g_hash(vec['m'] + h_hash(vec['ek']))[1].hex()}   (SHA3-512 bytes 32..63)",
        "",
        "Compressed ciphertext coefficients read back after done (host_slot / host_addr / host_dout),",
        "decoded from the NIST ciphertext — first 8 of each polynomial:",
        *[f"    slot {16 + i} u[{i}] (D=10): "
          + " ".join(str(x) for x in byte_decode(DU, vec['expected_c'][320 * i:320 * (i + 1)])[:8])
          for i in range(K)],
        f"    slot 19 v    (D=4) : "
        + " ".join(str(x) for x in byte_decode(DV, vec['expected_c'][960:])[:8]),
        "",
        "Shared secret K (32 B):",
        f"    NIST     : {vec['expected_k'].hex()}",
        f"    hardware : {k_hw.hex()}",
        "",
        f"Ciphertext c (1088 B): SHA3-256 NIST = {hashlib.sha3_256(vec['expected_c']).hexdigest()}",
        f"                       SHA3-256 hw   = {hashlib.sha3_256(c_hw).hexdigest()}",
        "Hardware c:", *hex_block(c_hw),
    ]
    write_log(f"{CASE}_result", lines)

    # ─── Phase timeline ──────────────────────────────────────────
    lines = [
        f"auto_encaps_ctrl phase timeline — NIST tcId={TC_ID}  (VCD: {CASE}.vcd, signal phase_name)",
        f"Total: {cycles} cycles from start to done",
        "",
        f"{'id':>2}  {'phase':<10} {'start(ns)':>10} {'end(ns)':>10} {'cycles':>7} {'share':>6}  说明",
        "-" * 96,
    ]
    for seg in phases.segments:
        name, desc = PHASES.get(seg['key'], ("?", ""))
        lines.append(f"{seg['key']:>2}  {name:<10} {seg['start_ns']:>10.0f} {seg['end_ns']:>10.0f} "
                     f"{seg['cycles']:>7} {100 * seg['cycles'] / cycles:>5.1f}%  {desc}")
    write_log(f"{CASE}_phases", lines)

    # ─── EXPAND_A entries ────────────────────────────────────────
    lines = [
        f"EXPAND_A — SampleNTT of the 9 entries of Â (NIST tcId={TC_ID}, signal expand_entry)",
        "Each entry absorbs rho || j || i into SHAKE-128 and keeps 12-bit candidates < q until",
        "256 coefficients are accepted, so the cycle count differs per entry (rejection sampling).",
        "",
        f"{'entry':>5}  {'A[i][j]':<8} {'start(ns)':>10} {'end(ns)':>10} {'cycles':>7}",
        "-" * 48,
    ]
    for seg in entries.segments:
        i, j = divmod(seg['key'], 3)
        lines.append(f"{seg['key']:>5}  A[{i}][{j}]   {seg['start_ns']:>10.0f} {seg['end_ns']:>10.0f} "
                     f"{seg['cycles']:>7}")
    write_log(f"{CASE}_expand_a", lines)

    # ─── POLY_OPS micro-ops ──────────────────────────────────────
    lines = [
        f"POLY_OPS — kyber_top micro-ops (NIST tcId={TC_ID}, signals poly_stage_name / poly_step / kt_op_name)",
        "",
    ]
    for stage, formula in POLY_STAGES:
        segs = [s for s in ops.segments if s['key'][0] == stage and s['key'][2] != "-"]
        if not segs:
            continue
        total = sum(s['cycles'] for s in segs)
        lines += ["", f"== {stage}   {formula}   ({total} cycles in micro-ops)",
                  f"   {'step':>4}  {'micro-op':<14} {'start(ns)':>10} {'cycles':>7}"]
        for s in segs:
            lines.append(f"   {s['key'][1]:>4}  {s['key'][2]:<14} {s['start_ns']:>10.0f} {s['cycles']:>7}")
    write_log(f"{CASE}_poly_ops", lines)

    dut._log.info(f"tcId={TC_ID}: {'PASS' if passed else 'FAIL'} in {cycles} cycles; "
                  f"logs in simulate_result/logs/{CASE}_*.txt")
    assert c_ok, "ciphertext mismatch vs NIST"
    assert k_ok, "shared secret K mismatch vs NIST"
