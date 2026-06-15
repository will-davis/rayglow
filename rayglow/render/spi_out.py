"""SPI frame output backend — ships packed bit-plane frames to the rp2350b.

On the split rig this host (rpi5) renders + packs (hub75.pack), and the RP2350
receives over SPI and drives the panels. The READY handshake makes it
self-pacing — we block until the rp2350b has armed its RX DMA, then push one
64 KB transfer.

rpi5-only deps (install on the Pi):  uv add spidev gpiozero lgpio
Prereqs: SPI enabled + /sys/module/spidev/parameters/bufsiz >= 65536 (so the
64 KB frame goes in ONE transfer; the rig is set to 131072).

Wiring (BCM):  MOSI=GPIO10(pin19)  SCLK=GPIO11(pin23)  CE0=GPIO8(pin24)
               READY input=GPIO25(pin22) <- rp2350b GP26 ;  common GND.
SPI mode 0, 8-bit MSB-first — matches the firmware PIO program (sample MOSI on
SCLK rising, shift-left, autopush per byte).
"""

from __future__ import annotations


class SpiOut:
    """Open SPI0 + the READY input line; `send(payload)` ships one frame."""

    def __init__(self, hz, ready_bcm=25, bus=0, dev=0):
        import spidev
        from gpiozero import DigitalInputDevice

        self.ready = DigitalInputDevice(ready_bcm, pull_up=False)
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.max_speed_hz = int(hz)
        self.spi.mode = 0
        self.spi.bits_per_word = 8
        print(f"spi_out: SPI{bus}.{dev} @ {self.spi.max_speed_hz/1e6:.2f} MHz "
              f"mode 0, READY=GPIO{ready_bcm}")

    def send(self, payload: bytes) -> None:
        """Wait for the rp2350b to be ready, then push the whole frame."""
        self.ready.wait_for_active()
        self.spi.writebytes2(payload)

    def close(self) -> None:
        try:
            self.spi.close()
        finally:
            self.ready.close()
