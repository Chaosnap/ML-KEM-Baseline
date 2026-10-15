"""Shared helpers for the evidence testbenches (tb/evidence_*).

The evidence testbenches run one known-answer case each, dump an annotated
VCD, and write human-readable logs to simulate_result/:

    simulate_result/waves/<case>.vcd     waveform (annotation signals are ASCII)
    simulate_result/logs/<case>_*.txt    result + timelines (sim time ↔ phase)

SegmentRecorder samples a signal every clock and records the time ranges in
which it held each value, so the logs can say "phase EXPAND_A runs from
t=… ns to t=… ns" — the same times you jump to in the VCD viewer.
"""

import os

import cocotb
from cocotb.triggers import FallingEdge
from cocotb.utils import get_sim_time

REPO_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
RESULT_DIR = os.path.join(REPO_DIR, 'simulate_result')
LOG_DIR = os.path.join(RESULT_DIR, 'logs')
WAVE_DIR = os.path.join(RESULT_DIR, 'waves')


def ascii_value(signal):
    """Decode an ASCII-string annotation signal (Verilog string reg)."""
    return signal.value.to_bytes(byteorder='big').lstrip(b'\x00').decode('ascii', 'replace').strip()


class SegmentRecorder:
    """Record contiguous time ranges during which key_fn() is constant.

    key_fn is called at every falling clock edge; returning None means
    "nothing to record" (e.g. engine idle). Each segment is a dict with
    key, start_ns, end_ns, start_cycle (from cycle_fn) and cycles, plus
    end_value (from end_fn, sampled on the first cycle after the segment).
    """

    def __init__(self, dut, key_fn, cycle_fn, end_fn=None):
        self.dut = dut
        self.key_fn = key_fn
        self.cycle_fn = cycle_fn
        self.end_fn = end_fn            # optional: sampled when a segment closes
        self.segments = []
        self._running = False

    def start(self):
        self._running = True
        cocotb.start_soon(self._run())
        return self

    def stop(self):
        self._running = False
        self._close(get_sim_time(unit='ns'))

    def _close(self, now_ns):
        if self.segments and self.segments[-1]['end_ns'] is None:
            seg = self.segments[-1]
            seg['end_ns'] = now_ns
            seg['cycles'] = self._cycles_between(seg['start_ns'], now_ns)
            if self.end_fn is not None:
                seg['end_value'] = self.end_fn()

    def _cycles_between(self, t0, t1):
        return int(round((t1 - t0) / self.clk_period_ns))

    async def _run(self):
        await FallingEdge(self.dut.clk)
        t_prev = get_sim_time(unit='ns')
        await FallingEdge(self.dut.clk)
        self.clk_period_ns = get_sim_time(unit='ns') - t_prev
        current = object()
        while self._running:
            key = self.key_fn()
            if key != current:
                # A key changes at the rising edge half a period before this sample
                now = get_sim_time(unit='ns') - self.clk_period_ns / 2
                self._close(now)
                if key is not None:
                    self.segments.append({'key': key, 'start_ns': now, 'end_ns': None,
                                          'start_cycle': self.cycle_fn(), 'cycles': None})
                current = key
            await FallingEdge(self.dut.clk)


def ensure_dirs():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(WAVE_DIR, exist_ok=True)


def write_log(name, lines):
    """Write simulate_result/logs/<name>.txt and return its repo-relative path."""
    ensure_dirs()
    path = os.path.join(LOG_DIR, name + '.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines).rstrip() + '\n')
    return os.path.relpath(path, REPO_DIR)


def hex_block(data, width=32):
    """Hex dump, `width` bytes per line, with byte offsets."""
    return [f"    {i:04x}: {data[i:i + width].hex()}" for i in range(0, len(data), width)]
