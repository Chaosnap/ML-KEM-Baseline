"""Host-side driver for the ML-KEM-768 FPGA UART command port.

Implements the request/response protocol of rtl/uart_host.v on top of any
byte transport with ``write(bytes)`` and ``read(n) -> bytes`` (a pyserial
``Serial`` object, or the simulated UART used by tb/fpga_top).

Low-level commands map 1:1 onto the protocol (ping, keygen, read_poly, ...).
High-level helpers (ml_kem_keygen, ml_kem_encaps, ml_kem_decaps) combine them
with the FIPS 203 encoding in ref/kyber_acvp.py to produce standard ek/dk/c/K
byte strings.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ref'))
from kyber_acvp import (  # noqa: E402
    K, DU, DV, DK_PKE_LEN, EK_LEN,
    byte_encode, byte_decode, h_hash, j_hash,
)

# ─── Protocol constants (keep in sync with rtl/uart_host.v) ──────────────

CMD_PING = 0x01
CMD_RESET = 0x02
CMD_KEYGEN = 0x10
CMD_ENCAPS = 0x20
CMD_DECRYPT = 0x30
CMD_READ_POLY = 0x40
CMD_WRITE_POLY = 0x41
CMD_READ_SECRET = 0x50

ST_OK = 0x00
ST_UNKNOWN_CMD = 0x01
ST_NO_CORE = 0x02
ST_BAD_ARG = 0x03
ST_BUSY = 0x04
ST_TIMEOUT = 0x05
ST_OVERFLOW = 0x06

STATUS_NAMES = {
    ST_OK: "OK",
    ST_UNKNOWN_CMD: "unknown command",
    ST_NO_CORE: "core not present",
    ST_BAD_ARG: "bad argument",
    ST_BUSY: "core busy",
    ST_TIMEOUT: "core timeout",
    ST_OVERFLOW: "RX overflow",
}

CORE_KEYGEN = 0
CORE_ENCAPS = 1
CORE_DECAPS = 2
CORE_NAMES = {CORE_KEYGEN: "keygen", CORE_ENCAPS: "encaps", CORE_DECAPS: "decaps"}

NUM_SLOTS = 20
PROTOCOL_VERSION = 0x01

# Bank slot map of each core (see rtl/auto_*_top.v, rtl/decaps_top.v)
KEYGEN_T_HAT_SLOTS = [0, 3, 6]
KEYGEN_S_HAT_SLOTS = [9, 10, 11]
ENCAPS_U_SLOTS = [16, 17, 18]
ENCAPS_V_SLOT = 19
DECAPS_U_SLOTS = [0, 1, 2]
DECAPS_V_SLOT = 3
DECAPS_S_HAT_SLOTS = [9, 10, 11]
DECAPS_M_SLOT = 4


class KyberUartError(Exception):
    """FPGA returned a non-OK status."""

    def __init__(self, cmd, status):
        self.cmd = cmd
        self.status = status
        name = STATUS_NAMES.get(status, "unknown status")
        super().__init__(f"command 0x{cmd:02x} failed: 0x{status:02x} ({name})")


class KyberUartTimeout(Exception):
    """FPGA did not answer in time (wrong port/baud, or bitstream not loaded)."""


class KyberUart:
    """Protocol client. ``transport`` needs write(bytes) and read(n) -> bytes."""

    def __init__(self, transport):
        self.t = transport
        self.caps = None
        self.version = None

    # ─── Construction ────────────────────────────────────────────

    @classmethod
    def open(cls, port, baud=115200, timeout=2.0):
        import serial  # pyserial; imported lazily so simulation needs no serial port
        ser = serial.Serial(port, baud, timeout=timeout)
        ser.reset_input_buffer()
        return cls(ser)

    def close(self):
        if hasattr(self.t, "close"):
            self.t.close()

    # ─── Transport helpers ───────────────────────────────────────

    def _read_exact(self, n):
        data = self.t.read(n)
        if len(data) != n:
            raise KyberUartTimeout(f"expected {n} bytes, got {len(data)}")
        return bytes(data)

    def _xfer(self, request, resp_len):
        """Send one request, return its payload (raises on non-OK status)."""
        self.t.write(bytes(request))
        status = self._read_exact(1)[0]
        if status != ST_OK:
            raise KyberUartError(request[0], status)
        return self._read_exact(resp_len) if resp_len else b""

    # ─── Low-level commands ──────────────────────────────────────

    def ping(self):
        """Return (version, caps). caps bit i = core i present."""
        resp = self._xfer([CMD_PING], 4)
        if resp[:2] != b"KY":
            raise KyberUartTimeout(f"bad PING signature {resp[:2]!r}")
        self.version, self.caps = resp[2], resp[3]
        return self.version, self.caps

    def has_core(self, core):
        if self.caps is None:
            self.ping()
        return bool(self.caps >> core & 1)

    def reset(self):
        self._xfer([CMD_RESET], 0)

    def keygen(self, d):
        """Run K-PKE.KeyGen on seed d (32 B). Returns core cycle count."""
        assert len(d) == 32
        return struct.unpack("<I", self._xfer(bytes([CMD_KEYGEN]) + d, 4))[0]

    def encaps(self, m, ek):
        """Run ML-KEM.Encaps_internal on m (32 B), ek (1184 B). Returns cycles."""
        assert len(m) == 32 and len(ek) == EK_LEN
        return struct.unpack("<I", self._xfer(bytes([CMD_ENCAPS]) + m + ek, 4))[0]

    def decrypt(self):
        """Run K-PKE.Decrypt on preloaded slots. Returns cycles."""
        return struct.unpack("<I", self._xfer([CMD_DECRYPT], 4))[0]

    def read_poly(self, core, slot):
        raw = self._xfer([CMD_READ_POLY, core, slot], 512)
        return list(struct.unpack("<256H", raw))

    def write_poly(self, core, slot, coeffs):
        assert len(coeffs) == 256
        self._xfer(bytes([CMD_WRITE_POLY, core, slot]) + struct.pack("<256H", *coeffs), 0)

    def read_secret(self, core):
        """32-byte register: rho (keygen core) or shared secret K (encaps core)."""
        return self._xfer([CMD_READ_SECRET, core], 32)

    # ─── High-level ML-KEM-768 operations ───────────────────────

    def ml_kem_keygen(self, d, z):
        """ML-KEM.KeyGen_internal(d, z) → (ek, dk, cycles)."""
        cycles = self.keygen(d)
        t_hat = [self.read_poly(CORE_KEYGEN, s) for s in KEYGEN_T_HAT_SLOTS]
        s_hat = [self.read_poly(CORE_KEYGEN, s) for s in KEYGEN_S_HAT_SLOTS]
        rho = self.read_secret(CORE_KEYGEN)
        ek = b"".join(byte_encode(12, p) for p in t_hat) + rho
        dk_pke = b"".join(byte_encode(12, p) for p in s_hat)
        dk = dk_pke + ek + h_hash(ek) + z
        return ek, dk, cycles

    def ml_kem_encaps(self, ek, m):
        """ML-KEM.Encaps_internal(ek, m) → (K, c, cycles)."""
        cycles = self.encaps(m, ek)
        u = [self.read_poly(CORE_ENCAPS, s) for s in ENCAPS_U_SLOTS]
        v = self.read_poly(CORE_ENCAPS, ENCAPS_V_SLOT)
        c = b"".join(byte_encode(DU, p) for p in u) + byte_encode(DV, v)
        k = self.read_secret(CORE_ENCAPS)
        return k, c, cycles

    def kpke_decrypt(self, dk_pke, c):
        """K-PKE.Decrypt(dk_pke, c) on the decaps core → (m, cycles)."""
        assert len(dk_pke) == DK_PKE_LEN and len(c) == 32 * (DU * K + DV)
        for i in range(K):
            self.write_poly(CORE_DECAPS, DECAPS_U_SLOTS[i],
                            byte_decode(DU, c[32 * DU * i: 32 * DU * (i + 1)]))
        self.write_poly(CORE_DECAPS, DECAPS_V_SLOT, byte_decode(DV, c[32 * DU * K:]))
        for i in range(K):
            self.write_poly(CORE_DECAPS, DECAPS_S_HAT_SLOTS[i],
                            byte_decode(12, dk_pke[384 * i: 384 * (i + 1)]))
        cycles = self.decrypt()
        m = byte_encode(1, self.read_poly(CORE_DECAPS, DECAPS_M_SLOT))
        return m, cycles

    def ml_kem_decaps(self, dk, c):
        """ML-KEM.Decaps_internal(dk, c) → (K, cycles).

        Decrypt runs on the decaps core; the Fujisaki-Okamoto re-encryption
        runs on the encaps core (which computes (K', r') = G(m' || H(ek)) and
        c' itself). Only the implicit-rejection hash J(z || c) is done here.
        """
        d = self.ml_kem_decaps_detail(dk, c)
        return d["k"], d["cycles"]

    def ml_kem_decaps_detail(self, dk, c):
        """ML-KEM.Decaps_internal with the intermediate values, as a dict:

        m_prime        decrypted message (FPGA decaps core)
        k_prime        re-encapsulation shared secret (FPGA encaps core)
        reencrypt_ok   c' == c, i.e. the ciphertext is valid (FPGA c' vs input c)
        k              returned key: k_prime if reencrypt_ok, else J(z || c)
                       (the implicit-rejection hash, computed on this host)
        dec_cycles, enc_cycles, cycles
        """
        dk_pke = dk[:DK_PKE_LEN]
        ek = dk[DK_PKE_LEN: DK_PKE_LEN + EK_LEN]
        h = dk[DK_PKE_LEN + EK_LEN: DK_PKE_LEN + EK_LEN + 32]
        z = dk[DK_PKE_LEN + EK_LEN + 32:]
        if h_hash(ek) != h:
            raise ValueError("dk hash check failed (H(ek) mismatch)")

        m_prime, dec_cycles = self.kpke_decrypt(dk_pke, c)
        k_prime, c_prime, enc_cycles = self.ml_kem_encaps(ek, m_prime)
        reencrypt_ok = c_prime == c
        return {
            "m_prime": m_prime,
            "k_prime": k_prime,
            "reencrypt_ok": reencrypt_ok,
            "k": k_prime if reencrypt_ok else j_hash(z + c),
            "dec_cycles": dec_cycles,
            "enc_cycles": enc_cycles,
            "cycles": dec_cycles + enc_cycles,
        }
