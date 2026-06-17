//! # rp2350-rgb-driver
//!
//! Zero-CPU HUB75 scan-out engine for the RP2350, ported and extended from
//! kjagiello's [`hub75-pio-rs`](https://github.com/kjagiello/hub75-pio-rs).
//! The CPU is never in the refresh loop: **3 PIO state machines + 4 DMA
//! channels** clock out binary-coded-modulation (BCM) frames continuously.
//!
//! ## Lineage / port status (PROJECT-PLAN §8)
//!
//! - **Phase 1** ported the RP2040 engine to RP2350 *unchanged in structure*
//!   (toolchain/PAC deltas only — see `dma.rs`).
//! - **Phase 2 (this file)** makes the one structural change the project hinges
//!   on: **widen the data path from one HUB75 chain to two parallel chains.**
//!   The 6-bit-per-pixel `u8` framebuffer cell becomes a **12-bit-per-pixel
//!   `u16`** cell carrying *both* chains; the data PIO drives `out pins, 16`
//!   over 12 consecutive GPIO (GP0–11). Both chains share the address lines and
//!   clock simultaneously — parallel chains cost nothing in refresh time, which
//!   is the whole lever (§4).
//!
//! ### Two-chain geometry
//!
//! Both chains are 1:16-scan `H`-tall strips that latch together, so the
//! drawable wall is **`W` × `2H`**: chain A is the top `H` rows, chain B the
//! bottom `H`. A single 64×32 panel per chain → a 64×64 wall; four daisy-chained
//! per chain → the full 256×64. A single-chain setup is the degenerate case:
//! draw only the top `H` rows and leave GP6–11 (chain B) idle.
//!
//! ### Framebuffer cell (`u16`) bit layout — matches the GP0–11 `out pins` group
//!
//! ```text
//!  bit:  15..12 │ 11 10  9 │  8  7  6 │  5  4  3 │  2  1  0
//!        unused │ B2 G2 R2 │ B1 G1 R1 │ B2 G2 R2 │ B1 G1 R1
//!               │   chain B (GP6–11)  │   chain A (GP0–5)
//!               │  R2G2B2  R1G1B1     │  R2G2B2  R1G1B1
//! ```
//! (R1G1B1 = the panel's top-half pixel, R2G2B2 = the bottom-half pixel under
//! 1:16 scan, exactly as in the original.)
//!
//! ## Requirements
//!
//! The 12 RGB outputs must be assigned to **consecutive** GPIO (the PIO
//! `out pins` group), and the address pins consecutive among themselves.

#![no_std]
#![feature(generic_const_exprs)]
#![allow(incomplete_features)]

use crate::dma::{Channel, ChannelIndex, ChannelRegs};
use core::convert::TryInto;
use embedded_graphics::prelude::*;
use rp235x_hal::gpio::{DynPinId, Function, Pin, PullNone};
use rp235x_hal::pio::{
    Buffers, PIOBuilder, PIOExt, PinDir, ShiftDirection, StateMachineIndex, UninitStateMachine, PIO,
};

pub mod dma;
pub mod lut;
/// Single-chain `u8`-cell engine (half-size frames); two-chain path here is
/// unaffected. See `single.rs`.
pub mod single;

/// Number of HUB75 chains driven in parallel by the data SM.
pub const CHAINS: usize = 2;

/// Framebuffer size in **`u16` cells**.
///
/// One cell per (column, bit-plane, address-row); `H` is the per-chain height,
/// so there are `H/2` address-rows under 1:16 scan. Both chains pack into the
/// single cell, so the count is independent of `CHAINS`.
#[doc(hidden)]
pub const fn fb_cells(w: usize, h: usize, b: usize) -> usize {
    w * h / 2 * b
}

/// Computes an array with number of clock ticks to wait for every n-th color bit
const fn delays<const B: usize>() -> [u32; B] {
    let mut arr = [0; B];
    let mut i = 0;
    while i < arr.len() {
        arr[i] = (1 << i) - 1;
        i += 1;
    }
    arr
}

/// Backing storage for the framebuffers.
///
/// `W`, `H`, `B` are the **per-chain** width, height, and bit depth. The wall is
/// `W` × `2H`. Cells are `u16` (12 RGB bits, both chains — see the crate-level
/// bit-layout diagram). The DMA streams these cells to the data PIO as 32-bit
/// words (two cells / two pixels per word).
pub struct DisplayMemory<const W: usize, const H: usize, const B: usize>
where
    [(); fb_cells(W, H, B)]: Sized,
{
    fbptr: [u32; 1],
    fb0: [u16; fb_cells(W, H, B)],
    fb1: [u16; fb_cells(W, H, B)],
    delays: [u32; B],
    delaysptr: [u32; 1],
}

impl<const W: usize, const H: usize, const B: usize> DisplayMemory<W, H, B>
where
    [(); fb_cells(W, H, B)]: Sized,
{
    pub const fn new() -> Self {
        let fb0 = [0; fb_cells(W, H, B)];
        let fb1 = [0; fb_cells(W, H, B)];
        let fbptr: [u32; 1] = [0];
        let delays = delays();
        let delaysptr: [u32; 1] = [0];
        DisplayMemory {
            fbptr,
            fb0,
            fb1,
            delays,
            delaysptr,
        }
    }
}

/// Mapping between GPIO pins and HUB75 pins.
///
/// `rgb` are the **12 consecutive** RGB pins, ordered to match the cell bit
/// layout: `[A:R1,G1,B1,R2,G2,B2, B:R1,G1,B1,R2,G2,B2]` on GP0–11. `rgb[0]` is
/// the PIO `out pins` base. For a single-chain setup, wire only `rgb[0..6]`
/// (chain A); the engine still drives `rgb[6..12]` (chain B) — they just output
/// black.
///
/// ADDR_PINS: number of address pins (4 = 1:16/64×32, 5 = 1:32/64×64, 6 = 1:64).
pub struct DisplayPins<F: Function, const ADDR_PINS: usize = 4> {
    pub rgb: [Pin<DynPinId, F, PullNone>; 2 * 6],
    pub clk: Pin<DynPinId, F, PullNone>,
    pub addr: [Pin<DynPinId, F, PullNone>; ADDR_PINS],
    pub lat: Pin<DynPinId, F, PullNone>,
    pub oe: Pin<DynPinId, F, PullNone>,
}

/// The HUB75 display driver
pub struct Display<'a, CH1, const W: usize, const H: usize, const B: usize, C, const ADDR_PINS: usize = 4>
where
    [(); fb_cells(W, H, B)]: Sized,
    CH1: ChannelIndex,
    C: RgbColor,
{
    mem: &'static mut DisplayMemory<W, H, B>,
    fb_loop_ch: Channel<CH1>,
    benchmark: bool,
    brightness: u8,
    lut: &'a dyn lut::Lut<B, C>,
}

impl<'a, CH1, const W: usize, const H: usize, const B: usize, C, const ADDR_PINS: usize> Display<'a, CH1, W, H, B, C, ADDR_PINS>
where
    [(); fb_cells(W, H, B)]: Sized,
    CH1: ChannelIndex,
    C: RgbColor,
{
    /// Creates a new display and starts the zero-CPU refresh engine.
    ///
    /// # Arguments
    ///
    /// * `pins`: GPIO↔HUB75 mapping (12 consecutive RGB pins, see `DisplayPins`)
    /// * `pio_sms`: the three PIO state machines (data, row, OE)
    /// * `dma_chs`: the four DMA channels (fb-feed, fb-loop, oe-feed, oe-loop)
    /// * `data_clk_div`: data-SM clock divisor `(int, frac)` → pixel clock =
    ///   sys_clk / (2 * div). `(2, 0)` ≈ 37.5 MHz @ 150 MHz (validated clean over
    ///   4 chained panels, §11.7). Raise it to trade refresh headroom for SI.
    pub fn new<PE, SM0, SM1, SM2, CH0, CH2, CH3>(
        buffer: &'static mut DisplayMemory<W, H, B>,
        pins: DisplayPins<PE::PinFunction, ADDR_PINS>,
        pio_block: &mut PIO<PE>,
        pio_sms: (
            UninitStateMachine<(PE, SM0)>,
            UninitStateMachine<(PE, SM1)>,
            UninitStateMachine<(PE, SM2)>,
        ),
        dma_chs: (Channel<CH0>, Channel<CH1>, Channel<CH2>, Channel<CH3>),
        benchmark: bool,
        data_clk_div: (u16, u8),
        lut: &'a impl lut::Lut<B, C>,
    ) -> Self
    where
        PE: PIOExt,
        SM0: StateMachineIndex,
        SM1: StateMachineIndex,
        SM2: StateMachineIndex,
        CH0: ChannelIndex,
        CH2: ChannelIndex,
        CH3: ChannelIndex,
        C: RgbColor,
    {
        // Setup PIO SMs
        let (data_sm, row_sm, oe_sm) = pio_sms;

        // Data SM — drives the 12 RGB lines (two chains) + CLK (sideset).
        let (data_sm, data_sm_tx) = {
            let program_data = pio::pio_asm!(
                ".side_set 1",
                "out isr, 32    side 0b0",
                ".wrap_target",
                "mov x isr      side 0b0",
                // Wait for the row program to set the ADDR pins
                "pixel:",
                // Two-chain widening: 16 bits/pixel (12 mapped to GP0-11, top 4
                // discarded) keeps clean 32-bit autopull alignment — 2 pixels
                // per word, mirroring the original 8-bit/4-pixel scheme.
                "out pins, 16   side 0b0",
                "jmp x-- pixel  side 0b1", // clock out the pixel
                "irq 4          side 0b0", // tell the row program to set the next row
                "wait 1 irq 5   side 0b0",
                ".wrap",
            );
            let installed = pio_block.install(&program_data.program).unwrap();
            let (mut sm, _, mut tx) = PIOBuilder::from_installed_program(installed)
                // 12 consecutive RGB pins (both chains), base = rgb[0] = GP0.
                .out_pins(pins.rgb[0].id().num, 12)
                .side_set_pin_base(pins.clk.id().num)
                .clock_divisor_fixed_point(data_clk_div.0, data_clk_div.1)
                .out_shift_direction(ShiftDirection::Right)
                .autopull(true)
                .buffers(Buffers::OnlyTx)
                .build(data_sm);
            // All 12 RGB pins + CLK as outputs.
            let mut pindirs = [(0u8, PinDir::Output); 13];
            for (i, p) in pins.rgb.iter().enumerate() {
                pindirs[i] = (p.id().num, PinDir::Output);
            }
            pindirs[12] = (pins.clk.id().num, PinDir::Output);
            sm.set_pindirs(pindirs);
            // Configure the width of the screen
            tx.write((W - 1).try_into().unwrap());
            (sm, tx)
        };

        let row_sm = {
            // Row program — sets ADDR pins, pulses LATCH, choreographs IRQs.
            let program_data = pio::pio_asm!(
                ".side_set 1",
                "pull           side 0b0", // Pull the height / 2 into OSR
                "out isr, 32    side 0b0", // and move it to OSR
                "pull           side 0b0", // Pull the color depth - 1 into OSR
                ".wrap_target",
                "mov x, isr     side 0b0",
                "addr:",
                "mov pins, ~x   side 0b0", // Set the row address
                "mov y, osr     side 0b0",
                "row:",
                "wait 1 irq 4   side 0b0", // Wait until the data is clocked in
                "nop            side 0b1",
                "irq 6          side 0b1", // Display the latched data
                "irq 5          side 0b0", // Clock in next row
                "wait 1 irq 7   side 0b0", // Wait for the OE cycle to complete
                "jmp y-- row    side 0b0",
                "jmp x-- addr   side 0b0",
                ".wrap",
            );
            let installed = pio_block.install(&program_data.program).unwrap();
            let (mut sm, _, mut tx) = PIOBuilder::from_installed_program(installed)
                .out_pins(pins.addr[0].id().num, ADDR_PINS.try_into().unwrap())
                .side_set_pin_base(pins.lat.id().num)
                .clock_divisor_fixed_point(1, 1)
                .build(row_sm);
            // Dynamically build the pindirs array
            let mut pindirs = [(0u8, PinDir::Output); 7]; // Max 6 addr pins + 1 lat pin
            let mut idx = 0;
            for i in 0..ADDR_PINS {
                pindirs[idx] = (pins.addr[i].id().num, PinDir::Output);
                idx += 1;
            }
            pindirs[idx] = (pins.lat.id().num, PinDir::Output);
            sm.set_pindirs(pindirs[0..=idx].iter().copied());
            // Configure the height of the screen
            tx.write((H / 2 - 1).try_into().unwrap());
            // Configure the color depth
            tx.write((B - 1).try_into().unwrap());
            sm
        };

        let (oe_sm, oe_sm_tx) = {
            // OE / BCM engine — binary-weighted display intervals from delays[].
            let program_data = pio::pio_asm!(
                ".side_set 1",
                ".wrap_target",
                "out x, 32      side 0b1",
                "wait 1 irq 6   side 0b1",
                "delay:",
                "jmp x-- delay  side 0b0",
                "irq 7          side 0b1",
                ".wrap",
            );
            let installed = pio_block.install(&program_data.program).unwrap();
            let (mut sm, _, tx) = PIOBuilder::from_installed_program(installed)
                .side_set_pin_base(pins.oe.id().num)
                .clock_divisor_fixed_point(1, 1)
                .autopull(true)
                .buffers(Buffers::OnlyTx)
                .build(oe_sm);
            sm.set_pindirs([(pins.oe.id().num, PinDir::Output)]);
            (sm, tx)
        };

        // Setup DMA
        let (fb_ch, fb_loop_ch, oe_ch, oe_loop_ch) = dma_chs;

        // TODO: move this to a better place
        buffer.fbptr[0] = buffer.fb0.as_ptr() as u32;
        buffer.delaysptr[0] = buffer.delays.as_ptr() as u32;

        // Number of 32-bit words the framebuffer occupies (two u16 cells/word).
        let fb_words = (fb_cells(W, H, B) * core::mem::size_of::<u16>() / 4) as u32;

        // Framebuffer channel
        fb_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w
                // Increase the read addr as we progress through the buffer
                .incr_read()
                .bit(true)
                // Do not increase the write addr because we always want to write to PIO FIFO
                .incr_write()
                .bit(false)
                // Read 32 bits at a time
                .data_size()
                .size_word()
                // Setup PIO FIFO as data request trigger
                .treq_sel()
                .bits(data_sm_tx.dreq_value())
                // Turn off interrupts
                .irq_quiet()
                .bit(!benchmark)
                // Chain to the channel selecting the framebuffers
                .chain_to()
                .bits(CH1::id())
                // Enable the channel
                .en()
                .bit(true)
        });
        fb_ch
            .regs()
            .ch_read_addr()
            .write(|w| unsafe { w.bits(buffer.fbptr[0]) });
        fb_ch
            .regs()
            .ch_trans_count()
            .write(|w| unsafe { w.bits(fb_words) });
        fb_ch
            .regs()
            .ch_write_addr()
            .write(|w| unsafe { w.bits(data_sm_tx.fifo_address() as u32) });

        // Framebuffer loop channel
        fb_loop_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w
                // Do not increase the read addr. We always want to read a single value
                .incr_read()
                .bit(false)
                // Do not increase the write addr because we always want to write to PIO FIFO
                .incr_write()
                .bit(false)
                // Read 32 bits at a time
                .data_size()
                .size_word()
                // No pacing
                .treq_sel()
                .permanent()
                // Turn off interrupts
                .irq_quiet()
                .bit(true)
                // Chain it back to the channel sending framebuffer data
                .chain_to()
                .bits(CH0::id())
                // Start up the DMA channel
                .en()
                .bit(true)
        });
        fb_loop_ch
            .regs()
            .ch_read_addr()
            .write(|w| unsafe { w.bits(buffer.fbptr.as_ptr() as u32) });
        fb_loop_ch
            .regs()
            .ch_trans_count()
            .write(|w| unsafe { w.bits(1) });
        fb_loop_ch
            .regs()
            .ch_al2_write_addr_trig()
            .write(|w| unsafe { w.bits(fb_ch.regs().ch_read_addr().as_ptr() as u32) });

        // Output enable channel
        oe_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w
                // Increase the read addr as we progress through the buffer
                .incr_read()
                .bit(true)
                // Do not increase the write addr because we always want to write to PIO FIFO
                .incr_write()
                .bit(false)
                // Read 32 bits at a time
                .data_size()
                .size_word()
                // Setup PIO FIFO as data request trigger
                .treq_sel()
                .bits(oe_sm_tx.dreq_value())
                // Turn off interrupts
                .irq_quiet()
                .bit(true)
                // Chain to the channel selecting the framebuffers
                .chain_to()
                .bits(CH3::id())
                // Enable the channel
                .en()
                .bit(true)
        });
        oe_ch
            .regs()
            .ch_read_addr()
            .write(|w| unsafe { w.bits(buffer.delays.as_ptr() as u32) });
        oe_ch
            .regs()
            .ch_trans_count()
            .write(|w| unsafe { w.bits(buffer.delays.len().try_into().unwrap()) });
        oe_ch
            .regs()
            .ch_write_addr()
            .write(|w| unsafe { w.bits(oe_sm_tx.fifo_address() as u32) });

        // Output enable loop channel
        oe_loop_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w
                // Do not increase the read addr. We always want to read a single value
                .incr_read()
                .bit(false)
                // Do not increase the write addr because we always want to write to PIO FIFO
                .incr_write()
                .bit(false)
                // Read 32 bits at a time
                .data_size()
                .size_word()
                // No pacing
                .treq_sel()
                .permanent()
                // Turn off interrupts
                .irq_quiet()
                .bit(true)
                // Chain it back to the channel sending framebuffer data
                .chain_to()
                .bits(CH2::id())
                // Start up the DMA channel
                .en()
                .bit(true)
        });
        oe_loop_ch
            .regs()
            .ch_read_addr()
            .write(|w| unsafe { w.bits(buffer.delaysptr.as_ptr() as u32) });
        oe_loop_ch
            .regs()
            .ch_trans_count()
            .write(|w| unsafe { w.bits(buffer.delaysptr.len().try_into().unwrap()) });
        oe_loop_ch
            .regs()
            .ch_al2_write_addr_trig()
            .write(|w| unsafe { w.bits(oe_ch.regs().ch_read_addr().as_ptr() as u32) });

        data_sm.start();
        row_sm.start();
        oe_sm.start();

        Display {
            mem: buffer,
            fb_loop_ch,
            benchmark,
            brightness: 255,
            lut,
        }
    }

    fn fb_loop_busy(&self) -> bool {
        self.fb_loop_ch
            .regs()
            .ch_ctrl_trig()
            .read()
            .busy()
            .bit_is_set()
    }

    /// Flips the display buffers
    ///
    /// Has to be called once you have drawn something onto the currently inactive buffer.
    pub fn commit(&mut self) {
        if self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32) {
            self.mem.fbptr[0] = self.mem.fb1.as_ptr() as u32;
            while !self.benchmark && !self.fb_loop_busy() {}
            self.mem.fb0[0..].fill(0);
        } else {
            self.mem.fbptr[0] = self.mem.fb0.as_ptr() as u32;
            while !self.benchmark && !self.fb_loop_busy() {}
            self.mem.fb1[0..].fill(0);
        }
    }

    /// Swaps to the freshly-filled inactive buffer **without** `commit`'s
    /// anti-tear sync or buffer-clear.
    ///
    /// `commit` waits on `fb_loop_busy()` (so the engine has latched the new
    /// frame before the old one is cleared) and then zeroes the old buffer. That
    /// wait polls a ~1-cycle DMA busy window and, under a tight back-to-back
    /// commit cadence (e.g. the Phase-5/experimental SPI streaming loop), can
    /// miss the window and spin forever. `flip` sidesteps both: it only toggles
    /// the active framebuffer pointer (the refresh DMA re-reads it each frame).
    ///
    /// **Only safe when every pixel of the inactive buffer is rewritten each
    /// frame** — true for the SPI-RX path (the DMA fills all `fb_cells*2` bytes)
    /// and the bulk [`Display::render`] path. For partial drawing (`set_pixel`)
    /// use [`Display::commit`], which clears. Worst case here is a one-frame tear
    /// if the swap lands mid-scan; the buffer still holds a complete frame.
    pub fn flip(&mut self) {
        self.mem.fbptr[0] = if self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32) {
            self.mem.fb1.as_ptr() as u32
        } else {
            self.mem.fb0.as_ptr() as u32
        };
    }

    /// Paints a wall pixel. Coordinates are 0-indexed; the wall is `W` × `2H`,
    /// with `y < H` on **chain A** (top) and `y >= H` on **chain B** (bottom).
    ///
    /// Only the 3 RGB bits belonging to the pixel's chain+half are touched, so
    /// the two chains are independent.
    pub fn set_pixel(&mut self, x: usize, y: usize, color: C) {
        // Which chain this wall row lands on, and the row within that chain.
        let chain = y / H; // 0 = A, 1 = B
        let yc = y % H;
        // Original panel-mount inversion, applied within the chain + width.
        let x = W - 1 - x;
        let yc = H - 1 - yc;
        // Top half (R1G1B1) vs bottom half (R2G2B2) under 1:16 scan.
        let half = yc > (H / 2) - 1;
        // Cell bit position: chain A→0, chain B→6; +3 for the bottom half.
        let shift = (chain * 6) + if half { 3 } else { 0 };
        let (c_r, c_g, c_b) = self.lut.lookup(color);
        let c_r: u16 = ((c_r as f32) * (self.brightness as f32 / 255f32)) as u16;
        let c_g: u16 = ((c_g as f32) * (self.brightness as f32 / 255f32)) as u16;
        let c_b: u16 = ((c_b as f32) * (self.brightness as f32 / 255f32)) as u16;
        let base_idx = x + ((yc % (H / 2)) * W * B);
        for b in 0..B {
            // Extract the n-th bit of each component of the color and pack them
            let cr = c_r >> b & 0b1;
            let cg = c_g >> b & 0b1;
            let cb = c_b >> b & 0b1;
            let packed_rgb = cb << 2 | cg << 1 | cr; // u16
            let idx = base_idx + b * W;
            if self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32) {
                self.mem.fb1[idx] &= !(0b111 << shift);
                self.mem.fb1[idx] |= packed_rgb << shift;
            } else {
                self.mem.fb0[idx] &= !(0b111 << shift);
                self.mem.fb0[idx] |= packed_rgb << shift;
            }
        }
    }

    pub fn set_brightness(&mut self, brightness: u8) {
        self.brightness = brightness
    }

    /// Phase-5 streaming hook: raw pointer to the **inactive** framebuffer — the
    /// one not currently being scanned out — so an external SPI-RX DMA channel
    /// can fill it directly (zero CPU on the ingest path, mirroring the refresh
    /// engine). The buffer is `fb_cells(W, H, B) * 2` bytes and its content must
    /// match exactly what [`Display::render`] would produce (the host packer in
    /// `rayglow/render/hub75.py` is proven byte-identical to `render`).
    ///
    /// After the DMA fill completes, call [`Display::commit`] to flip it in.
    /// **Recompute this pointer after every `commit()`** — the active/inactive
    /// roles swap, so last frame's destination is now on screen.
    pub fn inactive_fb_ptr(&mut self) -> *mut u16 {
        if self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32) {
            self.mem.fb1.as_mut_ptr()
        } else {
            self.mem.fb0.as_mut_ptr()
        }
    }

    /// Scales the BCM/OE display intervals (the `delays` table) by `gain` to
    /// reclaim brightness on a wide, **clock-bound** wall.
    ///
    /// The native intervals are `2^i − 1` ticks/plane (binary weighting). On a
    /// wide wall the per-plane *pixel-shift* time dwarfs the LED on-time (e.g.
    /// 256 px ≈ 6.8 µs shift vs ≤0.85 µs lit), so the panel sits dark most of
    /// each plane and looks dim. Because shifting overlaps display, that shift
    /// window is mostly *dead* time: raising `gain` fills it, so brightness
    /// climbs ~linearly while the refresh rate stays ~unchanged — up until the
    /// top plane's on-time approaches the shift time (≈ `gain` 8 at 256 px /
    /// 37.5 MHz). Beyond that you trade refresh rate for more brightness.
    /// `gain = 1` restores the native weighting. Ratios stay binary, so color
    /// is unaffected.
    pub fn set_oe_gain(&mut self, gain: u32) {
        for i in 0..B {
            self.mem.delays[i] = ((1u32 << i) - 1) * gain;
        }
    }

    /// Bulk frame repack (PROJECT-PLAN §5.4) — the fast path that replaces
    /// per-pixel `set_pixel` for full-frame animation / streaming.
    ///
    /// Calls `f(x, y)` for every wall pixel (`W` × `2H`) and expands the result
    /// into all `B` bit-planes of the **inactive** buffer in one pass. It is
    /// faster than calling `set_pixel` in a loop because it: (a) resolves the
    /// inactive buffer **once** instead of per pixel, (b) does **one** gamma LUT
    /// lookup per pixel instead of per plane, and (c) skips the per-pixel
    /// brightness float math when brightness is full. Because every pixel is
    /// written, the whole frame is overwritten — no pre-clear needed.
    ///
    /// Call [`Display::commit`] afterwards to flip the frame in.
    pub fn render<F>(&mut self, mut f: F)
    where
        F: FnMut(usize, usize) -> C,
    {
        // Resolve the inactive buffer once (set_pixel branches on this per pixel).
        let active_is_fb0 = self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32);
        let fb: &mut [u16] = if active_is_fb0 {
            &mut self.mem.fb1
        } else {
            &mut self.mem.fb0
        };
        let lut = self.lut;
        let brightness = self.brightness;

        for y in 0..(2 * H) {
            // Per-row constants: chain, half, address-row, and the cell bit shift.
            let chain = y / H;
            let yc = H - 1 - (y % H); // panel-mount inversion (matches set_pixel)
            let half = yc > (H / 2) - 1;
            let shift = (chain * 6) + if half { 3 } else { 0 };
            let mask = !(0b111u16 << shift);
            let row_base = (yc % (H / 2)) * W * B;

            for x in 0..W {
                let (mut c_r, mut c_g, mut c_b) = lut.lookup(f(x, y));
                if brightness != 255 {
                    let s = brightness as u32;
                    c_r = ((c_r as u32 * s) / 255) as u16;
                    c_g = ((c_g as u32 * s) / 255) as u16;
                    c_b = ((c_b as u32 * s) / 255) as u16;
                }
                let base = (W - 1 - x) + row_base;
                for b in 0..B {
                    let packed = (((c_b >> b) & 1) << 2
                        | ((c_g >> b) & 1) << 1
                        | ((c_r >> b) & 1))
                        << shift;
                    let idx = base + b * W;
                    fb[idx] = (fb[idx] & mask) | packed;
                }
            }
        }
    }
}

impl<'a, CH1, const W: usize, const H: usize, const B: usize, C, const ADDR_PINS: usize> OriginDimensions
    for Display<'a, CH1, W, H, B, C, ADDR_PINS>
where
    [(); fb_cells(W, H, B)]: Sized,
    CH1: ChannelIndex,
    C: RgbColor,
{
    fn size(&self) -> Size {
        // The wall spans both chains vertically: W x 2H.
        Size::new(W.try_into().unwrap(), (2 * H).try_into().unwrap())
    }
}

impl<'a, CH1, const W: usize, const H: usize, const B: usize, C, const ADDR_PINS: usize> DrawTarget
    for Display<'a, CH1, W, H, B, C, ADDR_PINS>
where
    [(); fb_cells(W, H, B)]: Sized,
    CH1: ChannelIndex,
    C: RgbColor,
{
    type Color = C;
    type Error = core::convert::Infallible;

    fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
    where
        I: IntoIterator<Item = Pixel<Self::Color>>,
    {
        let wall_h: i32 = (2 * H).try_into().unwrap();
        let wall_w: i32 = W.try_into().unwrap();
        for Pixel(coord, color) in pixels.into_iter() {
            if coord.x >= 0 && coord.y >= 0 && coord.x < wall_w && coord.y < wall_h {
                self.set_pixel(coord.x as usize, coord.y as usize, color);
            }
        }

        Ok(())
    }
}
