# Third-party notices — firmware

The RayGLow firmware is a **port** of kjagiello's `hub75-pio-rs` to the RP2350
(`rp235x-hal`). `firmware/src/lib.rs`, `firmware/src/dma.rs`, and `firmware/src/lut.rs`
derive directly from it — the 3-PIO-state-machine + 4-DMA scan-out engine, the DMA
register access, and the CIE/gamma LUT. Its MIT license requires its copyright notice
to travel with the derived work, reproduced in full below.

See [`../ATTRIBUTION.md`](../ATTRIBUTION.md) for the broader prior-art credits (pitschu,
hzeller) that informed the design but were not copied.

---

## kjagiello/hub75-pio-rs — MIT

<https://github.com/kjagiello/hub75-pio-rs>

```
The MIT License (MIT)

Copyright (c) 2022 Krzysztof Jagiello

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
