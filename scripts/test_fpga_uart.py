#!/usr/bin/env python3
"""Test a programmed FPGA over UART against the Python ML-KEM-768 oracle.

Usage:
    python scripts/test_fpga_uart.py --list                       # show serial ports
    python scripts/test_fpga_uart.py --port /dev/tty.usbserial-XXXX1
    python scripts/test_fpga_uart.py --port COM5 -n 20 --acvp     # more vectors + NIST ACVP

Tests (cores missing from the bitstream are skipped, see PING caps):
    link        PING, protocol version, capability mask
    errors      unknown command / bad slot / absent core → error status, link stays in sync
    loopback    WRITE_POLY then READ_POLY on every present core
    keygen      ML-KEM.KeyGen on random seeds vs. ref/kyber_acvp.py
    encaps      ML-KEM.Encaps on random (ek, m) vs. ref/kyber_acvp.py
    decaps      ML-KEM.Decaps (valid + tampered ciphertext → implicit rejection)
    roundtrip   FPGA KeyGen → FPGA Encaps → FPGA Decaps, shared secrets must match
    acvp        (--acvp) NIST ML-KEM-768 vectors, if cached by ref/test_acvp_oracle.py

--report [DIR] runs the ACVP vectors plus negative controls and writes an
evidence report (report.txt, fig_results.svg, fig_cycles.svg, report.json)
to DIR/acvp_<timestamp>/ (default DIR: hardware_result/).

Exit status is 0 only if every executed test passed.
"""

import argparse
import json
import os
import random
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kyber_uart import (  # noqa: E402
    KyberUart, KyberUartError, KyberUartTimeout,
    CORE_KEYGEN, CORE_ENCAPS, CORE_DECAPS, CORE_NAMES, NUM_SLOTS, PROTOCOL_VERSION,
    CMD_READ_POLY, ST_UNKNOWN_CMD, ST_BAD_ARG, ST_NO_CORE,
)
from kyber_acvp import keygen_full, encaps_full, decaps_full  # noqa: E402

VECTORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ref', 'acvp_vectors')
DEFAULT_REPORT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   '..', 'hardware_result'))


class Skip(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════
# Individual tests — each takes (dev, args, rng) and raises on failure
# ═══════════════════════════════════════════════════════════════════

def need(dev, *cores):
    missing = [CORE_NAMES[c] for c in cores if not dev.has_core(c)]
    if missing:
        raise Skip(f"core(s) not in bitstream: {', '.join(missing)}")


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"first diff at byte {i}: got 0x{x:02x}, expected 0x{y:02x}"
    return f"length {len(a)} vs {len(b)}"


def test_link(dev, args, rng):
    version, caps = dev.ping()
    cores = [CORE_NAMES[c] for c in range(3) if caps >> c & 1]
    print(f"    version=0x{version:02x} caps=0b{caps:03b} ({', '.join(cores) or 'none'})")
    assert version == PROTOCOL_VERSION, f"protocol version 0x{version:02x} != 0x{PROTOCOL_VERSION:02x}"
    assert caps != 0, "no cores present"
    for _ in range(5):
        assert dev.ping() == (version, caps)


def expect_status(fn, status):
    try:
        fn()
    except KyberUartError as e:
        assert e.status == status, f"expected status 0x{status:02x}, got 0x{e.status:02x}"
        return
    raise AssertionError(f"expected status 0x{status:02x}, got OK")


def test_errors(dev, args, rng):
    expect_status(lambda: dev._xfer([0x7F], 0), ST_UNKNOWN_CMD)
    core = next(c for c in range(3) if dev.has_core(c))
    expect_status(lambda: dev._xfer([CMD_READ_POLY, core, NUM_SLOTS], 0), ST_BAD_ARG)
    expect_status(lambda: dev._xfer([CMD_READ_POLY, 3, 0], 0), ST_NO_CORE)
    absent = [c for c in range(3) if not dev.has_core(c)]
    if absent:
        # Rejected WRITE_POLY must still consume its 512-byte payload
        expect_status(lambda: dev.write_poly(absent[0], 0, [0] * 256), ST_NO_CORE)
    dev.ping()  # stream still in sync


def test_loopback(dev, args, rng):
    for core in range(3):
        if not dev.has_core(core):
            continue
        for slot in (0, NUM_SLOTS - 1):
            coeffs = [rng.randrange(4096) for _ in range(256)]
            dev.write_poly(core, slot, coeffs)
            back = dev.read_poly(core, slot)
            assert back == coeffs, f"{CORE_NAMES[core]} slot {slot} loopback mismatch"


def test_keygen(dev, args, rng):
    need(dev, CORE_KEYGEN)
    for i in range(args.num):
        d, z = rng.randbytes(32), rng.randbytes(32)
        ek, dk, cycles = dev.ml_kem_keygen(d, z)
        ek_ref, dk_ref = keygen_full(d, z)
        assert ek == ek_ref, f"vector {i}: ek mismatch, {first_diff(ek, ek_ref)}"
        assert dk == dk_ref, f"vector {i}: dk mismatch, {first_diff(dk, dk_ref)}"
        print(f"    [{i + 1}/{args.num}] ok ({cycles} cycles)")


def test_encaps(dev, args, rng):
    need(dev, CORE_ENCAPS)
    for i in range(args.num):
        ek, _ = keygen_full(rng.randbytes(32), rng.randbytes(32))
        m = rng.randbytes(32)
        k, c, cycles = dev.ml_kem_encaps(ek, m)
        k_ref, c_ref = encaps_full(ek, m)
        assert c == c_ref, f"vector {i}: ciphertext mismatch, {first_diff(c, c_ref)}"
        assert k == k_ref, f"vector {i}: K mismatch"
        print(f"    [{i + 1}/{args.num}] ok ({cycles} cycles)")


def test_decaps(dev, args, rng):
    need(dev, CORE_ENCAPS, CORE_DECAPS)
    for i in range(args.num):
        ek, dk = keygen_full(rng.randbytes(32), rng.randbytes(32))
        _, c = encaps_full(ek, rng.randbytes(32))
        bad = bytearray(c)
        bad[rng.randrange(len(bad))] ^= 1 << rng.randrange(8)
        for label, ct in (("valid", c), ("tampered", bytes(bad))):
            k, cycles = dev.ml_kem_decaps(dk, ct)
            assert k == decaps_full(dk, ct), f"vector {i} ({label}): K mismatch"
        print(f"    [{i + 1}/{args.num}] ok (valid + implicit rejection)")


def test_roundtrip(dev, args, rng):
    need(dev, CORE_KEYGEN, CORE_ENCAPS, CORE_DECAPS)
    for i in range(args.num):
        ek, dk, _ = dev.ml_kem_keygen(rng.randbytes(32), rng.randbytes(32))
        k_enc, c, _ = dev.ml_kem_encaps(ek, rng.randbytes(32))
        k_dec, _ = dev.ml_kem_decaps(dk, c)
        assert k_enc == k_dec, f"round {i}: shared secrets differ"
        print(f"    [{i + 1}/{args.num}] ok")


def load_acvp(prompt_file, results_file, function=None):
    prompt_path = os.path.join(VECTORS_DIR, prompt_file)
    results_path = os.path.join(VECTORS_DIR, results_file)
    if not os.path.exists(prompt_path):
        raise Skip("ACVP vectors not cached (run: python ref/test_acvp_oracle.py)")
    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(results_path) as f:
        results = json.load(f)
    expected = {(g["tgId"], tc["tcId"]): tc
                for g in results["testGroups"] for tc in g["tests"]}
    for g in prompt["testGroups"]:
        if g.get("parameterSet") != "ML-KEM-768":
            continue
        if function and g.get("function") != function:
            continue
        for tc in g["tests"]:
            yield g, tc, expected[(g["tgId"], tc["tcId"])]


ACVP_SETS = ("keygen", "encaps", "decaps")


def resync(dev):
    """Recover the link after a host-side read timeout."""
    time.sleep(0.3)                     # FPGA drops a stalled request after 100 ms
    try:
        dev.t.reset_input_buffer()
    except AttributeError:
        pass
    try:
        dev.reset()
    except Exception:
        pass


def _check(name, expected_hex, actual):
    expected = bytes.fromhex(expected_hex)
    return {"name": name, "expected": expected.hex(), "actual": actual.hex(),
            "match": actual == expected}


def _record(category, tc_id, inputs, checks, cycles, path=None):
    ok = all(c["match"] for c in checks)
    bad = [c for c in checks if not c["match"]]
    detail = (f"{cycles} cycles" if ok else
              "; ".join(f"{c['name']} mismatch, " +
                        first_diff(bytes.fromhex(c['actual']), bytes.fromhex(c['expected']))
                        for c in bad))
    return {"category": category, "tcId": tc_id, "ok": ok, "detail": detail,
            "cycles": cycles, "path": path,
            "inputs": [{"name": n, "hex": v.hex()} for n, v in inputs],
            "checks": checks}


def acvp_cases(dev, sets, limit=None):
    """Yield (category, tcId, run) for every applicable ML-KEM-768 ACVP vector.

    run(mutate=None) executes the vector on the FPGA and returns a record dict
    (inputs, NIST-expected vs FPGA-actual per output field, cycles, ok).
    mutate(name, data) -> data lets a negative control alter one input.
    """
    def take(gen):
        for i, item in enumerate(gen):
            if limit is not None and i >= limit:
                return
            yield item

    same = lambda name, data: data
    if "keygen" in sets and dev.has_core(CORE_KEYGEN):
        for _, tc, exp in take(load_acvp("keygen_prompt.json", "keygen_results.json")):
            def run(mutate=same, tc=tc, exp=exp):
                d = mutate("d", bytes.fromhex(tc["d"]))
                z = mutate("z", bytes.fromhex(tc["z"]))
                ek, dk, cyc = dev.ml_kem_keygen(d, z)
                return _record("keyGen", tc["tcId"], [("d", d), ("z", z)],
                               [_check("ek", exp["ek"], ek), _check("dk", exp["dk"], dk)], cyc)
            yield "keyGen", tc["tcId"], run
    if "encaps" in sets and dev.has_core(CORE_ENCAPS):
        for _, tc, exp in take(load_acvp("encapdecap_prompt.json", "encapdecap_results.json",
                                         "encapsulation")):
            def run(mutate=same, tc=tc, exp=exp):
                ek = mutate("ek", bytes.fromhex(tc["ek"]))
                m = mutate("m", bytes.fromhex(tc["m"]))
                k, c, cyc = dev.ml_kem_encaps(ek, m)
                return _record("encapsulation", tc["tcId"], [("ek", ek), ("m", m)],
                               [_check("c", exp["c"], c), _check("K", exp["k"], k)], cyc)
            yield "encapsulation", tc["tcId"], run
    if "decaps" in sets and dev.has_core(CORE_ENCAPS) and dev.has_core(CORE_DECAPS):
        for g, tc, exp in take(load_acvp("encapdecap_prompt.json", "encapdecap_results.json",
                                         "decapsulation")):
            def run(mutate=same, g=g, tc=tc, exp=exp):
                dk = mutate("dk", bytes.fromhex(tc.get("dk") or g["dk"]))
                c = mutate("c", bytes.fromhex(tc["c"]))
                d = dev.ml_kem_decaps_detail(dk, c)
                path = "valid" if d["reencrypt_ok"] else "implicit_rejection"
                return _record("decapsulation", tc["tcId"], [("dk", dk), ("c", c)],
                               [_check("K", exp["k"], d["k"])], d["cycles"], path)
            yield "decapsulation", tc["tcId"], run


def _run_case(dev, category, tc_id, run, mutate=None):
    """Run one ACVP case; never raises for FPGA errors (they become a failed record)."""
    t0 = time.time()
    try:
        rec = run(mutate) if mutate else run()
    except (KyberUartError, KyberUartTimeout) as e:
        if isinstance(e, KyberUartTimeout):
            resync(dev)
        rec = {"category": category, "tcId": tc_id, "ok": False, "cycles": None,
               "path": None, "inputs": [], "checks": [],
               "detail": str(e) if isinstance(e, KyberUartError) else f"no response ({e})"}
    rec["seconds"] = round(time.time() - t0, 3)
    return rec


# Negative controls: flip one input bit and require the NIST comparison to FAIL.
MUTATIONS = {"keyGen": "d", "encapsulation": "m", "decapsulation": "c"}


def acvp_negative_controls(dev, sets):
    """Show the comparison can fail: one bit-flipped input per category."""
    controls = []
    for category, input_name in MUTATIONS.items():
        case = next(((cat, tc, run) for cat, tc, run in acvp_cases(dev, sets, limit=1)
                     if cat == category), None)
        if case is None:
            continue
        _, tc_id, run = case

        def flip(name, data, input_name=input_name):
            if name != input_name:
                return data
            return bytes([data[0] ^ 0x01]) + data[1:]

        rec = _run_case(dev, category, tc_id, run, mutate=flip)
        controls.append({
            "category": category, "tcId": tc_id,
            "mutation": f"{input_name}[0] ^= 0x01 (one bit flipped)",
            "compared": ", ".join(c["name"] for c in rec["checks"]) or "-",
            "detected": not rec["ok"],
            "detail": rec["detail"],
        })
    return controls


def test_acvp(dev, args, rng):
    if not args.acvp:
        raise Skip("enable with --acvp")
    sets = getattr(args, "acvp_set", None) or ACVP_SETS
    limit = getattr(args, "acvp_limit", None)
    records = []
    args.acvp_records = records                 # picked up by the report writer
    for category, tc_id, run in acvp_cases(dev, sets, limit):
        rec = _run_case(dev, category, tc_id, run)
        records.append(rec)
        if getattr(args, "verbose", False) or not rec["ok"]:
            path = f" [{rec['path']}]" if rec.get("path") else ""
            print(f"    {'ok  ' if rec['ok'] else 'FAIL'} {category:14s} tcId={tc_id:<3} {rec['detail']}{path}")
    if not records:
        raise Skip("no applicable ACVP vectors for the cores present")

    counts = {}
    for r in records:
        passed, total = counts.get(r["category"], (0, 0))
        counts[r["category"]] = (passed + r["ok"], total + 1)
    for category, (passed, total) in counts.items():
        print(f"    ML-KEM-768 {category:14s} {passed}/{total} ok")

    if getattr(args, "report", None):
        print("    negative controls (one input bit flipped, comparison must fail):")
        args.acvp_controls = acvp_negative_controls(dev, sets)
        for c in args.acvp_controls:
            print(f"      {'detected' if c['detected'] else 'NOT DETECTED'}: {c['category']} "
                  f"tcId={c['tcId']} {c['mutation']}")

    failures = [f"{r['category']} tcId={r['tcId']}" for r in records if not r["ok"]]
    missed = [c for c in getattr(args, "acvp_controls", []) if not c["detected"]]
    assert not failures, f"{len(failures)} ACVP vector(s) failed: {', '.join(failures)}"
    assert not missed, f"{len(missed)} negative control(s) not detected"
    print(f"    total {len(records)} NIST ACVP vectors ok")


TESTS = [
    ("link", test_link),
    ("errors", test_errors),
    ("loopback", test_loopback),
    ("keygen", test_keygen),
    ("encaps", test_encaps),
    ("decaps", test_decaps),
    ("roundtrip", test_roundtrip),
    ("acvp", test_acvp),
]


def run_tests(dev, args):
    """Run the selected tests on an open KyberUart. Returns number of failures."""
    rng = random.Random(args.seed)
    selected = [t for t in TESTS if not args.only or t[0] in args.only]
    results = []
    for name, fn in selected:
        print(f"[ RUN  ] {name}")
        t0 = time.time()
        try:
            fn(dev, args, rng)
            outcome = "PASS"
        except Skip as e:
            outcome, detail = "SKIP", str(e)
        except (AssertionError, KyberUartError, KyberUartTimeout, ValueError) as e:
            outcome, detail = "FAIL", str(e) or traceback.format_exc()
            if isinstance(e, KyberUartTimeout):
                resync(dev)
        dt = time.time() - t0
        if outcome == "PASS":
            print(f"[ PASS ] {name} ({dt:.2f}s)")
        else:
            print(f"[ {outcome} ] {name}: {detail}")
        results.append((name, outcome))

    passed = sum(o == "PASS" for _, o in results)
    failed = sum(o == "FAIL" for _, o in results)
    skipped = sum(o == "SKIP" for _, o in results)
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port, e.g. /dev/tty.usbserial-XXXX1 or COM5")
    ap.add_argument("--baud", type=int, default=115200, help="must match fpga_top BAUD (default 115200)")
    ap.add_argument("--timeout", type=float, default=2.0, help="serial read timeout in seconds")
    ap.add_argument("-n", "--num", type=int, default=3, help="random vectors per test (default 3)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible vectors")
    ap.add_argument("--acvp", action="store_true", help="also run cached NIST ACVP vectors")
    ap.add_argument("--acvp-set", nargs="+", choices=ACVP_SETS,
                    help="ACVP categories to run (default: all)")
    ap.add_argument("--acvp-limit", type=int, default=None, metavar="N",
                    help="run only the first N ACVP vectors of each category")
    ap.add_argument("--report", nargs="?", const=DEFAULT_REPORT_DIR, default=None, metavar="DIR",
                    help="write a text + SVG + JSON evidence report of the ACVP run "
                         f"(implies --acvp; default dir: {os.path.relpath(DEFAULT_REPORT_DIR)})")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every ACVP vector")
    ap.add_argument("--only", nargs="+", choices=[n for n, _ in TESTS], help="run only these tests")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    args = ap.parse_args()

    if args.list:
        from serial.tools import list_ports
        for p in list_ports.comports():
            print(f"{p.device:30s} {p.description}")
        return 0
    if not args.port:
        ap.error("--port is required (use --list to see available ports)")

    if args.seed is None:
        args.seed = random.randrange(1 << 32)
    if args.report:
        args.acvp = True
    print(f"Port {args.port} @ {args.baud} baud, seed={args.seed}")

    dev = KyberUart.open(args.port, args.baud, args.timeout)
    started = time.time()
    try:
        failed = run_tests(dev, args)
        if args.report and getattr(args, "acvp_records", None):
            import acvp_report
            run_dir = acvp_report.write_report(
                args.report, args.acvp_records, getattr(args, "acvp_controls", []),
                acvp_report.collect_provenance(dev, args, started))
            print(f"\nReport written to {run_dir}/")
            for name in ("report.txt", "fig_results.svg", "fig_cycles.svg", "report.json"):
                print(f"  {name}")
        return 1 if failed else 0
    finally:
        dev.close()


if __name__ == "__main__":
    sys.exit(main())
