# UART hardware test scripts

Host-side tools for testing a programmed FPGA (`rtl/fpga_top.v`) over its
USB-UART port. 中文测试指南：[docs/fpga_uart_testing_zh.md](../docs/fpga_uart_testing_zh.md)

| File | Purpose |
|------|---------|
| `kyber_uart.py` | Protocol driver (`KyberUart`): low-level commands + ML-KEM KeyGen/Encaps/Decaps helpers |
| `test_fpga_uart.py` | Test runner: compares the FPGA against the Python oracle in `ref/kyber_acvp.py` |
| `requirements.txt` | `pyserial` |

## 1. Build and program the bitstream (Arty A7-100T)

In the Vivado **Tcl Console**:

```tcl
source /path/to/ML-KEM-Baseline/fpga/build.tcl     ;# creates + builds + saves fpga/vivado_proj/ml_kem_uart.xpr
source /path/to/ML-KEM-Baseline/fpga/program.tcl   ;# programs the board over JTAG
```

`build.tcl` creates the project, runs synthesis, implementation and bitstream, and
leaves the project open and saved. Reopen it later with *File > Open Project*.
It prints WNS/WHS at the end, and **timing must be met**. The cores are not
pipelined (the worst path, `basemul_unit`, is about 41 ns), so the design runs at
20 MHz from an MMCM (`mmcm_div` in the settings block). All three cores are
enabled. Settings such as baud rate and enabled cores are at the top of `build.tcl`.

After programming, LED0 blinks (heartbeat). LED1 = core busy, LED2 = request in
progress, LED3 = last response was an error.

## 2. Run the tests

```bash
pip install -r requirements-test.txt -r scripts/requirements.txt
python scripts/test_fpga_uart.py --list                  # find the port
python scripts/test_fpga_uart.py --port /dev/tty.usbserial-XXXXXXXX1
python scripts/test_fpga_uart.py --port COM5 -n 20 --seed 1 --acvp
python scripts/test_fpga_uart.py --port COM5 --only link loopback
```

Digilent boards expose two serial ports on the FT2232. The UART is usually the
**second** one (e.g. ending in `1` on macOS/Linux, the higher COM number on
Windows). The runner prints `PASS`/`FAIL`/`SKIP` for each test; tests that
need a core missing from the bitstream are skipped. `--acvp` needs the vectors
cached by `python ref/test_acvp_oracle.py`.

## 3. Using the driver directly

```python
from kyber_uart import KyberUart
dev = KyberUart.open("/dev/tty.usbserial-XXXXXXXX1")
print(dev.ping())                                   # (version, caps)
ek, dk, cycles = dev.ml_kem_keygen(d=bytes(32), z=bytes(32))
K, c, cycles = dev.ml_kem_encaps(ek, m=bytes(32))
K2, cycles = dev.ml_kem_decaps(dk, c)               # needs encaps + decaps cores
```

## Protocol

8N1, 115200 baud by default (`BAUD` generic of `fpga_top`). Request
`[cmd][args][payload]` → response `[status][payload]`. Multi-byte values are
little-endian, and polynomials are 256 × u16. The full table is in the header
of `rtl/uart_host.v`.

| cmd | Name | Args | Request payload | Response payload |
|-----|------|------|-----------------|------------------|
| 0x01 | PING | – | – | `'K' 'Y' version caps` |
| 0x02 | RESET | – | – | – |
| 0x10 | KEYGEN | – | d (32 B) | cycles (u32) |
| 0x20 | ENCAPS | – | m ‖ ek (1216 B) | cycles (u32) |
| 0x30 | DECRYPT | – | – | cycles (u32) |
| 0x40 | READ_POLY | core, slot | – | 512 B |
| 0x41 | WRITE_POLY | core, slot | 512 B | – |
| 0x50 | READ_SECRET | core | – | 32 B (rho / K) |

Status: `00` OK, `01` unknown command, `02` core not present, `03` bad argument,
`04` busy, `05` core timeout, `06` RX overflow. If a request stalls mid-frame for
100 ms, the FPGA drops it without a response and resets the cores.

The whole flow is also simulated: `make test_fpga_top` runs `test_fpga_uart.py`
against `fpga_top` in Icarus through a bit-level UART model.
