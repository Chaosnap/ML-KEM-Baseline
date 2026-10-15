"""Evidence report for the NIST ACVP run on the FPGA (used by test_fpga_uart.py --report).

Writes one folder per run, <report dir>/acvp_<timestamp>/, containing:

    report.txt        formal plain-text test report (English)
    fig_results.svg   result matrix: one cell per NIST test case
    fig_cycles.svg    hardware latency per function (one panel each)
    report.json       raw record: inputs, NIST-expected and FPGA-actual outputs
                      (full hex + SHA-256), cycles, negative controls, provenance

No third-party packages are needed; the figures are plain SVG.

Provenance recorded: NIST vector files (SHA-256, vsId/mode/revision/isSample,
source URL), git commit, bitstream SHA-256 + timing summary + a staleness check
against the RTL sources, serial link and FPGA protocol version.
"""

import datetime
import glob
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
VECTOR_DIR = os.path.join(REPO, 'ref', 'acvp_vectors')
VECTOR_FILES = ['keygen_prompt', 'keygen_results', 'encapdecap_prompt', 'encapdecap_results']
BITSTREAM = os.path.join(REPO, 'fpga', 'vivado_proj', 'ml_kem_uart.runs', 'impl_1', 'fpga_top.bit')
TIMING_RPT = os.path.join(REPO, 'fpga', 'vivado_proj', 'reports', 'timing.rpt')
BUILD_TCL = os.path.join(REPO, 'fpga', 'build.tcl')


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _mtime(path):
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(timespec='seconds')


def _git(*args):
    try:
        return subprocess.run(['git', '-C', REPO] + list(args), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def _vector_provenance():
    try:
        sys.path.insert(0, os.path.join(REPO, 'ref'))
        from test_acvp_oracle import VECTOR_URLS
    except Exception:
        VECTOR_URLS = {}
    out = []
    for name in VECTOR_FILES:
        path = os.path.join(VECTOR_DIR, name + '.json')
        if not os.path.exists(path):
            continue
        with open(path) as f:
            meta = json.load(f)
        out.append({
            'file': os.path.relpath(path, REPO),
            'sha256': _sha256_file(path),
            'bytes': os.path.getsize(path),
            'url': VECTOR_URLS.get(name),
            'vsId': meta.get('vsId'), 'algorithm': meta.get('algorithm'),
            'mode': meta.get('mode'), 'revision': meta.get('revision'),
            'isSample': meta.get('isSample'),
        })
    return out


def _timing():
    if not os.path.exists(TIMING_RPT):
        return None
    text = open(TIMING_RPT, errors='replace').read()
    m = re.search(r'WNS\(ns\)\s+TNS\(ns\).*?\n\s*-+.*?\n\s*(\S+)\s+(\S+)\s+\S+\s+\S+\s+(\S+)\s+(\S+)', text)
    met = 'All user specified timing constraints are met' in text
    return {'report': os.path.relpath(TIMING_RPT, REPO), 'modified': _mtime(TIMING_RPT),
            'wns_ns': float(m.group(1)) if m else None, 'whs_ns': float(m.group(3)) if m else None,
            'all_constraints_met': met}


def _bitstream():
    if not os.path.exists(BITSTREAM):
        return None
    rtl = glob.glob(os.path.join(REPO, 'rtl', '*.v')) + glob.glob(os.path.join(REPO, 'rtl', '*.vh'))
    bit_t = os.path.getmtime(BITSTREAM)
    newer = sorted(os.path.relpath(p, REPO) for p in rtl if os.path.getmtime(p) > bit_t)
    return {'file': os.path.relpath(BITSTREAM, REPO), 'sha256': _sha256_file(BITSTREAM),
            'modified': _mtime(BITSTREAM), 'rtl_newer_than_bitstream': newer}


def _clock_mhz():
    try:
        m = re.search(r'set\s+mmcm_div\s+(\d+)', open(BUILD_TCL).read())
        return 1000.0 / int(m.group(1)) if m else None
    except OSError:
        return None


def _command_line():
    argv = [a for a in sys.argv if a]
    if not argv:
        return '(not run from the command line)'
    script = os.path.relpath(argv[0], REPO) if os.path.exists(argv[0]) else argv[0]
    return ' '.join(['python', script] + argv[1:])


def collect_provenance(dev, args, started):
    try:
        import serial
        pyserial = serial.__version__
    except Exception:
        pyserial = None
    status = _git('status', '--porcelain', '--untracked-files=no')
    return {
        'generated_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'duration_s': round(time.time() - started, 1),
        'command': _command_line(),
        'host': {'platform': platform.platform(), 'python': platform.python_version(),
                 'pyserial': pyserial},
        'link': {'port': args.port, 'baud': args.baud,
                 'fpga_protocol_version': getattr(dev, 'version', None),
                 'fpga_caps': getattr(dev, 'caps', None)},
        'git': {'commit': _git('rev-parse', 'HEAD'), 'subject': _git('log', '-1', '--format=%s'),
                'branch': _git('rev-parse', '--abbrev-ref', 'HEAD'),
                'uncommitted_tracked_files': len(status.splitlines()) if status else 0},
        'clock_mhz': _clock_mhz(),
        'bitstream': _bitstream(),
        'timing': _timing(),
        'vectors': _vector_provenance(),
        'acvp_sets': list(getattr(args, 'acvp_set', None) or ['keygen', 'encaps', 'decaps']),
        'acvp_limit': getattr(args, 'acvp_limit', None),
    }




# ═══════════════════════════════════════════════════════════════════
# Report writer
# ═══════════════════════════════════════════════════════════════════

CAT_NAME = {'keyGen': 'KeyGen', 'encapsulation': 'Encaps', 'decapsulation': 'Decaps'}
CAT_ORDER = ['keyGen', 'encapsulation', 'decapsulation']
ACVP_FUNC = {'keyGen': 'keyGen', 'encapsulation': 'encapsulation', 'decapsulation': 'decapsulation'}


def _add_digests(records):
    for r in records:
        for c in r.get('checks', []):
            c['expected_sha256'] = hashlib.sha256(bytes.fromhex(c['expected'])).hexdigest()
            c['actual_sha256'] = hashlib.sha256(bytes.fromhex(c['actual'])).hexdigest()
            c['bytes'] = len(c['expected']) // 2


def _summary(records, controls):
    cats = {}
    for r in records:
        c = cats.setdefault(r['category'], {'passed': 0, 'total': 0, 'cycles': []})
        c['total'] += 1
        c['passed'] += bool(r['ok'])
        if r.get('cycles'):
            c['cycles'].append(r['cycles'])
    out = {}
    for k, v in cats.items():
        cyc = sorted(v['cycles'])
        out[k] = {'passed': v['passed'], 'total': v['total'],
                  'min_cycles': cyc[0] if cyc else None, 'max_cycles': cyc[-1] if cyc else None,
                  'median_cycles': cyc[len(cyc) // 2] if cyc else None}
    return {
        'passed': sum(bool(r['ok']) for r in records), 'total': len(records),
        'categories': out,
        'implicit_rejection': sum(r.get('path') == 'implicit_rejection' for r in records),
        'controls_detected': sum(bool(c['detected']) for c in controls),
        'controls_total': len(controls),
        'all_ok': bool(records) and all(r['ok'] for r in records)
                  and all(c['detected'] for c in controls),
    }


def write_report(out_dir, records, controls, provenance):
    """Write report.txt, fig_results.svg, fig_cycles.svg, report.json; return the folder."""
    _add_digests(records)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = os.path.join(out_dir, f'acvp_{stamp}')
    os.makedirs(run_dir, exist_ok=True)
    summary = _summary(records, controls)
    data = {'provenance': provenance, 'summary': summary, 'records': records,
            'negative_controls': controls}
    with open(os.path.join(run_dir, 'report.json'), 'w') as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(run_dir, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(_text_report(data))
    with open(os.path.join(run_dir, 'fig_results.svg'), 'w', encoding='utf-8') as f:
        f.write(_svg_results(data))
    with open(os.path.join(run_dir, 'fig_cycles.svg'), 'w', encoding='utf-8') as f:
        f.write(_svg_cycles(data))
    return run_dir


# ─── Text report ─────────────────────────────────────────────────────

def _n(value):
    return f'{value:,}' if value is not None else '-'


def _latency(cycles, mhz):
    return f'{cycles / mhz / 1000:.3f} ms' if cycles and mhz else '-'


def _text_report(d):
    P, S, R, C = d['provenance'], d['summary'], d['records'], d['negative_controls']
    mhz = P.get('clock_mhz')
    W = 100
    rule, thin = '=' * W, '-' * W
    L = []
    add = L.append

    add(rule)
    add('ML-KEM-768 (FIPS 203)  --  NIST ACVP Known-Answer Test on FPGA Hardware')
    add('Test Report')
    add(rule)
    verdict = 'PASS' if S['all_ok'] else 'FAIL'
    add(f"Result             : {verdict}")
    add(f"Test cases         : {S['passed']} / {S['total']} match the NIST expected results byte-for-byte")
    add(f"Negative controls  : {S['controls_detected']} / {S['controls_total']} deliberate faults detected")
    add(f"Report generated   : {P['generated_at']}   (run time {P['duration_s']} s)")
    add('')

    add('1. SCOPE')
    add(thin)
    add('Algorithm          : ML-KEM (FIPS 203), parameter set ML-KEM-768')
    counts = ', '.join(f"{ACVP_FUNC[k]} ({S['categories'][k]['total']})"
                       for k in CAT_ORDER if k in S['categories'])
    add(f'ACVP functions     : {counts}')
    add('Device under test  : FPGA (Digilent Arty A7-100T), driven over UART by the host')
    add('Reference results  : NIST ACVP expectedResults.json, used as published. The comparison')
    add('                     does not use the project\'s own Python reference implementation.')
    add('')

    add('2. TEST VECTORS (usnistgov/ACVP-Server, gen-val/json-files)')
    add(thin)
    add(f"{'File':<26}{'vsId':>5}  {'mode':<11}{'revision':<9}{'isSample':<9}SHA-256")
    for v in P['vectors']:
        add(f"{os.path.basename(v['file']):<26}{v['vsId']:>5}  {v['mode']:<11}{v['revision']:<9}"
            f"{str(v['isSample']):<9}{v['sha256']}")
    for v in P['vectors']:
        if v.get('url'):
            add(f"  source: {v['url']}")
    add('')

    add('3. TEST ENVIRONMENT')
    add(thin)
    b, t, g, lk = P.get('bitstream'), P.get('timing'), P['git'], P['link']
    add(f"Bitstream          : {b['file'] if b else 'not found'}")
    if b:
        add(f"  SHA-256          : {b['sha256']}")
        add(f"  built            : {b['modified']}")
        add(f"  RTL newer than bitstream: {', '.join(b['rtl_newer_than_bitstream']) or 'none'}")
    if t:
        add(f"Timing (Vivado)    : WNS {t['wns_ns']} ns, WHS {t['whs_ns']} ns, "
            f"{'all constraints met' if t['all_constraints_met'] else 'CONSTRAINTS NOT MET'}")
    add(f"Core clock         : {mhz} MHz (MMCM)" if mhz else 'Core clock         : unknown')
    add(f"Source revision    : {g['commit'] or 'unknown'} ({g['branch']})")
    add(f"  last commit      : {g['subject']}")
    add(f"  uncommitted tracked files: {g['uncommitted_tracked_files']}")
    add(f"Serial link        : {lk['port']} @ {lk['baud']} baud, 8N1")
    add(f"FPGA protocol      : version 0x{(lk['fpga_protocol_version'] or 0):02x}, "
        f"cores 0b{(lk['fpga_caps'] or 0):03b} (bit0 KeyGen, bit1 Encaps, bit2 Decaps)")
    add(f"Host               : {P['host']['platform']}, Python {P['host']['python']}, "
        f"pyserial {P['host']['pyserial']}")
    add(f"Command            : {P['command']}")
    add('')

    add('4. METHOD')
    add(thin)
    for line in [
        '1) Each test input is read from the NIST prompt file (keyGen: d, z; encapsulation: ek, m;',
        '   decapsulation: dk, c) and sent to the FPGA over UART.',
        '2) The FPGA computes the function in hardware (NTT, sampling, SHA-3/SHAKE all on chip).',
        '3) The host reads the results back and serialises them per FIPS 203 (ByteEncode) into',
        '   ek, dk, c or K. For dk the host appends H(ek) and z as specified.',
        '4) Every output is compared byte-for-byte with the NIST expectedResults file.',
        '5) Decapsulation: the FPGA decrypts c and re-encrypts (Fujisaki-Okamoto check c\' == c).',
        '   If c\' != c (invalid ciphertext), the implicit-rejection key K-bar = J(z || c) is',
        '   computed on the host. Such cases are marked "implicit rejection" in section 6.',
        '6) Negative controls: one input bit is flipped for one case per function; the',
        '   comparison against the unmodified NIST result must then FAIL.',
    ]:
        add(line)
    add('')

    add('5. RESULTS SUMMARY')
    add(thin)
    add(f"{'Function':<16}{'Passed':>8}{'Total':>8}  {'Result':<8}"
        f"{'Min cycles':>12}{'Median':>10}{'Max':>10}{'Median latency':>18}")
    for k in CAT_ORDER:
        c = S['categories'].get(k)
        if not c:
            continue
        res = 'PASS' if c['passed'] == c['total'] else 'FAIL'
        add(f"{ACVP_FUNC[k]:<16}{c['passed']:>8}{c['total']:>8}  {res:<8}"
            f"{_n(c['min_cycles']):>12}{_n(c['median_cycles']):>10}{_n(c['max_cycles']):>10}"
            f"{_latency(c['median_cycles'], mhz):>18}")
    add(f"{'Total':<16}{S['passed']:>8}{S['total']:>8}  {'PASS' if S['passed'] == S['total'] else 'FAIL':<8}")
    add('Cycles: from the last input byte to completion (UART transfer excluded).')
    add('Decapsulation cycles = decrypt + re-encrypt.')
    add('')

    add('6. PER-TEST-CASE RESULTS')
    add(thin)
    add('For each compared output: length, and the first 16 hex digits of SHA-256 of the NIST')
    add('expected value and of the FPGA output. Full values are in report.json.')
    add('')
    add(f"{'Function':<15}{'tcId':>5}  {'Result':<7}{'Output':<7}{'Bytes':>6}  "
        f"{'SHA-256 (NIST)':<17}{'SHA-256 (FPGA)':<17}{'Cycles':>8}  Note")
    for r in R:
        note = {'valid': 'valid ciphertext',
                'implicit_rejection': 'implicit rejection (K-bar on host)'}.get(r.get('path'), '')
        if not r['checks']:
            add(f"{ACVP_FUNC[r['category']]:<15}{r['tcId']:>5}  {'FAIL':<7}{'-':<7}{'-':>6}  "
                f"{'-':<17}{'-':<17}{'-':>8}  {r['detail']}")
            continue
        for i, c in enumerate(r['checks']):
            first = i == 0
            add(f"{ACVP_FUNC[r['category']] if first else '':<15}{r['tcId'] if first else '':>5}  "
                f"{('PASS' if r['ok'] else 'FAIL') if first else '':<7}{c['name']:<7}{c['bytes']:>6}  "
                f"{c['expected_sha256'][:16]:<17}{c['actual_sha256'][:16]:<17}"
                f"{format(r['cycles'], ',') if first and r['cycles'] else '':>8}  "
                f"{(note if first else '')}{'' if c['match'] else '  MISMATCH'}")
    add('')

    add('7. NEGATIVE CONTROLS')
    add(thin)
    if C:
        add(f"{'Function':<15}{'tcId':>5}  {'Fault injected':<34}{'Compared':<10}Outcome")
        for c in C:
            add(f"{ACVP_FUNC[c['category']]:<15}{c['tcId']:>5}  {c['mutation']:<34}{c['compared']:<10}"
                f"{'mismatch detected (expected)' if c['detected'] else 'NOT DETECTED'}")
    else:
        add('Not run.')
    add('')

    add('8. NOTES')
    add(thin)
    samples = [os.path.basename(v['file']) for v in P['vectors'] if v.get('isSample')]
    if samples:
        add(f"- {', '.join(samples)} carry \"isSample\": true. These are the sample vector sets")
        add('  published by NIST in the ACVP-Server repository together with their expected results.')
    add(f"- {S['implicit_rejection']} decapsulation case(s) exercised implicit rejection.")
    if b and b['rtl_newer_than_bitstream']:
        add('- WARNING: RTL sources are newer than the bitstream; the board may not run the latest code.')
    add('')

    add('9. REPRODUCTION')
    add(thin)
    add('Verify the vector files against NIST:')
    for v in P['vectors']:
        if v.get('url'):
            add(f"  curl -sL {v['url']} | shasum -a 256")
    add('Re-run the test (programmed board required):')
    add(f"  {P['command']}")
    add(rule)
    return '\n'.join(L) + '\n'


# ─── Figures (plain SVG) ─────────────────────────────────────────────

INK, INK2, MUTED, GRID = '#0b0b0b', '#52514e', '#8a8984', '#e7e6e1'
ACCENT, GOOD, BAD = '#2a78d6', '#0ca30c', '#d03b3b'
FONT = "font-family='Helvetica Neue, Helvetica, Arial, sans-serif'"


def _svg(width, height, body):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
            f"viewBox='0 0 {width} {height}' {FONT}>\n"
            f"<rect width='100%' height='100%' fill='#ffffff'/>\n{body}</svg>\n")


def _text(x, y, s, size=13, fill=INK, anchor='start', weight='normal'):
    s = str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return (f"<text x='{x}' y='{y}' font-size='{size}' fill='{fill}' "
            f"text-anchor='{anchor}' font-weight='{weight}'>{s}</text>\n")


def _svg_results(d):
    """Result matrix: one cell per test case, grouped by function."""
    S, R, P = d['summary'], d['records'], d['provenance']
    cell, gap, left, top = 34, 4, 150, 104
    per_row = 25
    rows = [(k, [r for r in R if r['category'] == k]) for k in CAT_ORDER]
    rows = [(k, lst) for k, lst in rows if lst]
    controls = d['negative_controls']
    width = left + per_row * (cell + gap) + 30
    height = top + (len(rows) + bool(controls)) * (cell + 34) + 96 + (24 if controls else 0)
    b = ''
    b += _text(24, 36, 'ML-KEM-768 (FIPS 203): NIST ACVP known-answer tests on FPGA', 20, weight='bold')
    verdict = 'PASS' if S['all_ok'] else 'FAIL'
    b += _text(24, 62, f"{verdict}: {S['passed']}/{S['total']} test cases match the NIST expected results "
               f"byte-for-byte; {S['controls_detected']}/{S['controls_total']} negative controls detected",
               14, INK2)
    b += _text(24, 82, f"Device: Arty A7-100T @ {P.get('clock_mhz')} MHz, UART {P['link']['baud']} baud.  "
               f"Reference: NIST ACVP-Server expectedResults.json.  {P['generated_at']}", 12, MUTED)
    y = top
    for k, lst in rows:
        c = S['categories'][k]
        b += _text(24, y + cell / 2 + 5, CAT_NAME[k], 14, weight='bold')
        b += _text(24, y + cell / 2 + 21, f"{c['passed']}/{c['total']} pass", 12, INK2)
        for i, r in enumerate(lst):
            x = left + i * (cell + gap)
            color = GOOD if r['ok'] else BAD
            dash = " stroke-dasharray='3 2'" if r.get('path') == 'implicit_rejection' else ''
            fill_op = '0.16' if r.get('path') == 'implicit_rejection' else '1'
            b += (f"<rect x='{x}' y='{y}' width='{cell}' height='{cell}' rx='4' fill='{color}' "
                  f"fill-opacity='{fill_op}' stroke='{color}' stroke-width='1.5'{dash}/>\n")
            glyph_fill = color if r.get('path') == 'implicit_rejection' else '#ffffff'
            b += _text(x + cell / 2, y + cell / 2 + 5, '✓' if r['ok'] else '✕', 15, glyph_fill, 'middle', 'bold')
            b += _text(x + cell / 2, y + cell + 13, r['tcId'], 10, MUTED, 'middle')
        y += cell + 34
    if controls:
        detected = sum(bool(c['detected']) for c in controls)
        b += _text(24, y + cell / 2 + 5, 'Negative controls', 14, weight='bold')
        b += _text(24, y + cell / 2 + 21, f"{detected}/{len(controls)} detected", 12, INK2)
        for i, c in enumerate(controls):
            x = left + i * (cell + gap) * 2
            ok = c['detected']
            stroke = INK2 if ok else BAD
            b += (f"<rect x='{x}' y='{y}' width='{2 * cell + gap}' height='{cell}' rx='4' fill='#ffffff' "
                  f"stroke='{stroke}' stroke-width='1.5'/>\n")
            b += _text(x + cell + gap / 2, y + cell / 2 + 5, '≠ caught' if ok else 'MISSED', 12,
                       stroke, 'middle', 'bold')
            b += _text(x + cell + gap / 2, y + cell + 13, f"{CAT_NAME[c['category']]} {c['tcId']}", 10,
                       MUTED, 'middle')
        y += cell + 34

    # Legend
    ly = y + 10
    b += (f"<rect x='24' y='{ly}' width='16' height='16' rx='3' fill='{GOOD}' stroke='{GOOD}'/>\n")
    b += _text(32, ly + 12, '✓', 11, '#ffffff', 'middle', 'bold')
    b += _text(48, ly + 13, 'Output matches NIST expected result (number = ACVP tcId)', 12, INK2)
    b += (f"<rect x='24' y='{ly + 24}' width='16' height='16' rx='3' fill='{GOOD}' fill-opacity='0.16' "
          f"stroke='{GOOD}' stroke-width='1.5' stroke-dasharray='3 2'/>\n")
    b += _text(32, ly + 36, '✓', 11, GOOD, 'middle', 'bold')
    b += _text(48, ly + 37, 'Decaps with invalid ciphertext: FPGA re-encryption rejects c, '
               'implicit-rejection key J(z||c) computed on host', 12, INK2)
    ly2 = ly + 48
    if controls:
        b += (f"<rect x='24' y='{ly2}' width='16' height='16' rx='3' fill='#ffffff' stroke='{INK2}' "
              f"stroke-width='1.5'/>\n")
        b += _text(48, ly2 + 13, 'Negative control: one input bit flipped; the comparison with NIST '
                   'must fail, and it did (≠ caught)', 12, INK2)
        ly2 += 24
    if not S['all_ok']:
        b += (f"<rect x='24' y='{ly2}' width='16' height='16' rx='3' fill='{BAD}'/>\n")
        b += _text(32, ly2 + 12, '✕', 11, '#ffffff', 'middle', 'bold')
        b += _text(48, ly2 + 13, 'Mismatch against NIST expected result', 12, INK2)
        height += 24
    return _svg(width, height, b)


def _nice_step(span):
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if span / step <= 6:
            return step
    return 10000


def _svg_cycles(d):
    """One panel per function: each dot is a test case, with its own zoomed axis."""
    S, R, P = d['summary'], d['records'], d['provenance']
    mhz = P.get('clock_mhz')
    cats = [k for k in CAT_ORDER if any(r['category'] == k and r.get('cycles') for r in R)]
    width, left, right, panel_h, top = 900, 150, 40, 96, 92
    height = top + len(cats) * panel_h + 36
    b = ''
    b += _text(24, 36, 'Hardware latency per NIST test case', 20, weight='bold')
    b += _text(24, 60, 'Clock cycles from the last input byte to completion (UART transfer excluded). '
               'Each dot is one ACVP test case; the bar marks the median.', 13, INK2)
    b += _text(24, 78, 'Each panel has its own axis range; spread within a function comes from '
               'rejection sampling. Decaps = decrypt + re-encrypt.', 12, MUTED)
    plot_w = width - left - right
    for i, k in enumerate(cats):
        pts = [r for r in R if r['category'] == k and r.get('cycles')]
        vals = [r['cycles'] for r in pts]
        c = S['categories'][k]
        lo, hi = min(vals), max(vals)
        step = _nice_step(max(hi - lo, 20))
        a0 = (lo // step) * step - step
        a1 = (hi // step + 2) * step
        x = lambda v: left + (v - a0) / (a1 - a0) * plot_w
        y0 = top + i * panel_h
        cy = y0 + 34
        b += _text(24, cy - 2, CAT_NAME[k], 14, weight='bold')
        b += _text(24, cy + 15, f"n = {len(vals)}", 12, INK2)
        t = a0
        while t <= a1:
            b += f"<line x1='{x(t):.1f}' x2='{x(t):.1f}' y1='{cy - 22}' y2='{cy + 22}' stroke='{GRID}'/>\n"
            b += _text(f'{x(t):.1f}', cy + 38, f'{t:,}', 11, MUTED, 'middle')
            t += step
        for j, r in enumerate(sorted(pts, key=lambda r: r['cycles'])):
            jitter = ((j * 5) % 7 - 3) * 3.5
            color = ACCENT if r['ok'] else BAD
            b += (f"<circle cx='{x(r['cycles']):.1f}' cy='{cy + jitter:.1f}' r='4.5' fill='{color}' "
                  f"stroke='#ffffff' stroke-width='1.5'><title>tcId {r['tcId']}: {r['cycles']:,} cycles</title></circle>\n")
        med = c['median_cycles']
        b += f"<line x1='{x(med):.1f}' x2='{x(med):.1f}' y1='{cy - 20}' y2='{cy + 20}' stroke='{INK}' stroke-width='2'/>\n"
        lat = f" ({_latency(med, mhz)} at {mhz:g} MHz)" if mhz else ''
        spread = f"spread {hi - lo:,} cycles ({100 * (hi - lo) / med:.1f}%)"
        b += _text(width - right, cy - 26, f"median {med:,} cycles{lat}; range {lo:,} to {hi:,}; {spread}",
                   12, INK, 'end')
    b += _text(left + plot_w / 2, height - 10, 'Clock cycles', 12, INK2, 'middle')
    return _svg(width, height, b)
