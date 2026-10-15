"""End-to-end UART test of fpga_top.

Drives the serial pins of fpga_top with a bit-level UART model and runs the
same host code that talks to real hardware (scripts/kyber_uart.py and the
test suite in scripts/test_fpga_uart.py). The blocking host code runs in a
cocotb bridge thread; its serial reads/writes resume simulation coroutines.

The Makefile scales CLK_FREQ/BAUD so one UART bit is 8 clock cycles and the
RX/RUN timeouts are 100k cycles.
"""

import os
import sys
from argparse import Namespace
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.task import bridge, resume
from cocotb.triggers import ClockCycles, Event, FallingEdge, First, Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
from kyber_uart import (  # noqa: E402
    KyberUart, KyberUartError, CMD_KEYGEN, CMD_READ_POLY, CMD_WRITE_POLY,
    CORE_KEYGEN, ST_BAD_ARG,
)
import test_fpga_uart  # noqa: E402

CLK_PERIOD_NS = 10
CLKS_PER_BIT = 8
BIT_NS = CLK_PERIOD_NS * CLKS_PER_BIT
RX_TIMEOUT_CYCLES = 100_000
READ_TIMEOUT_NS = 3_000_000       # host-side read timeout (300k cycles)


class SimUart:
    """Serial-port stand-in: write()/read() are blocking, for bridge threads."""

    def __init__(self, dut):
        self.dut = dut
        self.rx = deque()
        self.rx_event = Event()
        dut.uart_rxd.value = 1
        cocotb.start_soon(self._monitor())
        self.write = resume(self._send)
        self.read = resume(self._recv)

    async def _monitor(self):
        """Decode bytes from fpga_top.uart_txd."""
        txd = self.dut.uart_txd
        while True:
            await FallingEdge(txd)
            await Timer(BIT_NS * 3 // 2, unit='ns')
            byte = 0
            for i in range(8):
                byte |= int(txd.value) << i
                await Timer(BIT_NS, unit='ns')
            assert int(txd.value) == 1, "UART TX framing error (stop bit low)"
            self.rx.append(byte)
            self.rx_event.set()

    async def _send(self, data):
        for byte in data:
            for bit in [0] + [(byte >> i) & 1 for i in range(8)] + [1]:
                self.dut.uart_rxd.value = bit
                await Timer(BIT_NS, unit='ns')

    async def _recv(self, n, timeout_ns=READ_TIMEOUT_NS):
        timer = Timer(timeout_ns, unit='ns')
        while len(self.rx) < n:
            self.rx_event.clear()
            if await First(self.rx_event.wait(), timer) is timer:
                break
        return bytes(self.rx.popleft() for _ in range(min(n, len(self.rx))))


async def init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    uart = SimUart(dut)
    dut.rst_btn.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_btn.value = 0
    await ClockCycles(dut.clk, 300)          # power-on reset stretch
    return KyberUart(uart)


@cocotb.test()
async def test_host_suite(dut):
    """Run scripts/test_fpga_uart.py tests (1 random vector each) over UART."""
    dev = await init(dut)
    args = Namespace(num=1, seed=2026, acvp=False, only=None)
    failures = await bridge(test_fpga_uart.run_tests)(dev, args)
    assert failures == 0, f"{failures} host-suite test(s) failed"


@cocotb.test()
async def test_rx_timeout_recovery(dut):
    """A request abandoned mid-payload is dropped; the link recovers."""
    dev = await init(dut)

    def partial_requests():
        # KEYGEN with 5 of 32 seed bytes (core started, then aborted + reset)
        dev.t.write(bytes([CMD_KEYGEN]) + bytes(5))
        # WRITE_POLY with a partial payload
        dev.t.write(bytes([CMD_WRITE_POLY, CORE_KEYGEN, 0]) + bytes(7))
        assert dev.t.read(1, 50_000) == b"", "unexpected response to partial request"

    await bridge(partial_requests)()
    await ClockCycles(dut.clk, RX_TIMEOUT_CYCLES + 1000)

    def recover():
        dev.ping()
        ek, dk, cycles = dev.ml_kem_keygen(bytes(range(32)), bytes(32))
        from kyber_acvp import keygen_full
        assert (ek, dk) == keygen_full(bytes(range(32)), bytes(32)), "keygen after abort"

    await bridge(recover)()


@cocotb.test()
async def test_error_led(dut):
    """LED[3] tracks whether the last response was an error."""
    dev = await init(dut)

    def bad_slot():
        try:
            dev._xfer([CMD_READ_POLY, CORE_KEYGEN, 25], 0)
        except KyberUartError as e:
            assert e.status == ST_BAD_ARG
        else:
            raise AssertionError("slot 25 accepted")

    await bridge(bad_slot)()
    assert dut.led.value.to_unsigned() & 0b1000, "error LED not set after error"

    await bridge(dev.ping)()
    assert not dut.led.value.to_unsigned() & 0b1000, "error LED still set after OK"
