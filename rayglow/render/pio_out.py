"""Parallel PIO transport — an SpiOut-compatible sink over the 4-lane RP1-PIO bus.

Drop-in for `SpiOut`: same `__init__` / `send(bytes)` / `close()` shape, so the
`run_spi` loop and `_SendPipe` use it unchanged. Data + clock are clocked out by
the Pi 5's RP1 PIO via `piobridge/libpioshim.so` (build it first — see
`piobridge/README.md`); CS framing + READY use gpiozero, exactly like SpiOut. The
byte stream is identical to the SPI path, so the firmware/packer don't change —
only the wire. Pairs with firmware bin `phase6-parallel`.

The shim is opened lazily on the first `send()` so the frame size (and thus the
DMA buffer) is taken from the actual payload, single- or two-chain alike.
"""
import ctypes
import os

# Per-byte nibble swap (high<->low). The RX shifts left so the FIRST nibble it
# samples becomes the byte's HIGH nibble; the Pi sends low-nibble-first under
# shift-right, so we pre-swap to make the framebuffer byte-identical to the
# packer. bytes.translate applies this 256-entry LUT in C (negligible per frame,
# and on the send worker thread so it overlaps the next render).
_NIBBLE_SWAP = bytes(((b << 4) | (b >> 4)) & 0xFF for b in range(256))


class PioOut:
    _LIB = "libpioshim.so"

    def __init__(self, clkdiv=4.0, ready_bcm=25, nibble_swap=True,
                 data0_gpio=12, clk_gpio=20, cs_gpio=21, lib_path=None):
        from gpiozero import DigitalInputDevice, DigitalOutputDevice

        path = lib_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "piobridge", self._LIB)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found — build it: `cd {os.path.dirname(path)} && make` "
                f"(see piobridge/README.md)")
        self._lib = ctypes.CDLL(path)
        self._lib.pioshim_open.restype = ctypes.c_void_p
        self._lib.pioshim_open.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                           ctypes.c_uint, ctypes.c_float]
        self._lib.pioshim_send.restype = ctypes.c_int
        self._lib.pioshim_send.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                           ctypes.c_uint]
        self._lib.pioshim_close.restype = None
        self._lib.pioshim_close.argtypes = [ctypes.c_void_p]

        self.clkdiv = float(clkdiv)
        self.nibble_swap = bool(nibble_swap)
        self.data0_gpio = int(data0_gpio)
        self.clk_gpio = int(clk_gpio)
        self._handle = None

        # READY: RP2350 -> Pi, active high (armed). CS: Pi -> RP2350, active low —
        # active_high=False makes cs.on() drive the line LOW (asserted); start
        # deasserted (idle high).
        self.ready = DigitalInputDevice(ready_bcm, pull_up=False)
        self.cs = DigitalOutputDevice(cs_gpio, active_high=False,
                                      initial_value=False)
        print(f"pio_out: 4-lane PIO bus — DATA0=GPIO{data0_gpio}, "
              f"CLK=GPIO{clk_gpio}, CS=GPIO{cs_gpio}, READY=GPIO{ready_bcm}, "
              f"clkdiv={self.clkdiv:g}")

    def _ensure_open(self, nbytes):
        if self._handle is not None:
            return
        h = self._lib.pioshim_open(self.data0_gpio, self.clk_gpio,
                                   nbytes, self.clkdiv)
        if not h:
            raise RuntimeError(
                "pioshim_open failed — is /dev/pio0 accessible, piolib built, "
                "and a PIO state machine free?")
        self._handle = h

    def send(self, payload):
        """Wait for READY, frame the burst with CS, clock the bytes out."""
        self._ensure_open(len(payload))
        if self.nibble_swap:
            payload = payload.translate(_NIBBLE_SWAP)
        self.ready.wait_for_active()        # RP2350 armed its RX DMA
        self.cs.on()                        # CS low — frame start
        rc = self._lib.pioshim_send(self._handle, payload, len(payload))
        self.cs.off()                       # CS high — frame end
        if rc != 0:
            raise RuntimeError(f"pioshim_send failed ({rc})")

    def close(self):
        try:
            if self._handle is not None:
                self._lib.pioshim_close(self._handle)
                self._handle = None
        finally:
            self.cs.close()
            self.ready.close()
