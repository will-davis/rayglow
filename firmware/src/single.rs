//! # Single-chain (`u8`) HUB75 engine — `Display1`
//!
//! A self-contained variant of the two-chain engine in `lib.rs`, for driving
//! **one** HUB75 chain with a **`u8` framebuffer cell** (6 RGB bits, one chain)
//! instead of the two-chain `u16` cell (12 bits, both chains). This is the
//! original kjagiello data path (8 bits/pixel, 4 pixels per 32-bit DMA word),
//! kept beside — not merged into — the `u16` engine so the **two-chain path
//! (`lib.rs`) stays byte-for-byte unchanged** for the production PCB.
//!
//! ## Why it exists
//! The single-chain stop-gap (one Adafruit HAT, all panels in series) ran on the
//! `u16` engine with chain B idle, which wastes half of every cell → a 128 KB
//! frame for 8 panels. With a `u8` cell that idle half is gone: 8 panels =
//! `fb_cells(512,32,8)` **bytes** = 64 KB, halving the SPI payload (and the dead
//! chain-B bits never travel the wire). Full 8-bit colour is retained.
//!
//! ## Geometry
//! The wall is **`W` × `H`** (one chain, `H`=32 under 1:16 scan: R1G1B1 drives
//! rows 0..15, R2G2B2 rows 16..31). There is no chain-B / `2H` dimension. Cell
//! layout & indexing are identical to `lib.rs` minus the chain offset:
//!   `idx = addr_row*(W*B) + plane*W + (W-1-x)`, value = `rgb3 << (half?3:0)`.
//! The host packer `rayglow/render/hub75.py::pack_single` is a 1:1 port of
//! `Display1::render` and is proven byte-identical by `tools/verify.py`.
//!
//! ## Requirements
//! 6 consecutive RGB GPIO (only chain A — `rgb[0..6]`); address pins consecutive.

use crate::dma::{Channel, ChannelIndex, ChannelRegs};
use crate::{fb_cells, lut};
use core::convert::TryInto;
use embedded_graphics::prelude::*;
use rp235x_hal::pio::{
    Buffers, PIOBuilder, PIOExt, PinDir, ShiftDirection, StateMachineIndex, UninitStateMachine, PIO,
};

use crate::DisplayPins;

/// Computes the binary-coded-modulation delay table (`2^i - 1` ticks/plane).
const fn delays<const B: usize>() -> [u32; B] {
    let mut arr = [0; B];
    let mut i = 0;
    while i < arr.len() {
        arr[i] = (1 << i) - 1;
        i += 1;
    }
    arr
}

/// Backing storage for the single-chain framebuffers. `W`/`H`/`B` are the chain
/// width, height and bit depth; the wall is `W` × `H`. Cells are **`u8`** (6 RGB
/// bits, one chain). The DMA streams them to the data PIO as 32-bit words (four
/// cells / four pixels per word).
pub struct DisplayMemory1<const W: usize, const H: usize, const B: usize>
where
    [(); fb_cells(W, H, B)]: Sized,
{
    fbptr: [u32; 1],
    fb0: [u8; fb_cells(W, H, B)],
    fb1: [u8; fb_cells(W, H, B)],
    delays: [u32; B],
    delaysptr: [u32; 1],
}

impl<const W: usize, const H: usize, const B: usize> DisplayMemory1<W, H, B>
where
    [(); fb_cells(W, H, B)]: Sized,
{
    pub const fn new() -> Self {
        DisplayMemory1 {
            fbptr: [0],
            fb0: [0; fb_cells(W, H, B)],
            fb1: [0; fb_cells(W, H, B)],
            delays: delays(),
            delaysptr: [0],
        }
    }
}

/// The single-chain HUB75 driver. Mirrors `lib.rs::Display` with a `u8` cell.
pub struct Display1<'a, CH1, const W: usize, const H: usize, const B: usize, C, const ADDR_PINS: usize = 4>
where
    [(); fb_cells(W, H, B)]: Sized,
    CH1: ChannelIndex,
    C: RgbColor,
{
    mem: &'static mut DisplayMemory1<W, H, B>,
    fb_loop_ch: Channel<CH1>,
    benchmark: bool,
    lut: &'a dyn lut::Lut<B, C>,
}

impl<'a, CH1, const W: usize, const H: usize, const B: usize, C, const ADDR_PINS: usize>
    Display1<'a, CH1, W, H, B, C, ADDR_PINS>
where
    [(); fb_cells(W, H, B)]: Sized,
    CH1: ChannelIndex,
    C: RgbColor,
{
    /// Creates the single-chain display and starts the zero-CPU refresh engine.
    /// `pins.rgb[0..6]` (chain A) drive the panel; `rgb[6..12]` are accepted for a
    /// shared `DisplayPins` but unused. Args mirror `lib.rs::Display::new`.
    pub fn new<PE, SM0, SM1, SM2, CH0, CH2, CH3>(
        buffer: &'static mut DisplayMemory1<W, H, B>,
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
        let (data_sm, row_sm, oe_sm) = pio_sms;

        // Data SM — single chain: 8 bits/pixel (6 RGB on GP0-5 + 2 unused on
        // GP6-7, driven 0), 4 pixels per 32-bit autopull word. Identical to the
        // two-chain program except `out pins, 8` (vs 16).
        let (data_sm, data_sm_tx) = {
            let program_data = pio::pio_asm!(
                ".side_set 1",
                "out isr, 32    side 0b0",
                ".wrap_target",
                "mov x isr      side 0b0",
                "pixel:",
                "out pins, 8    side 0b0",
                "jmp x-- pixel  side 0b1", // clock out the pixel
                "irq 4          side 0b0", // tell the row program to set the next row
                "wait 1 irq 5   side 0b0",
                ".wrap",
            );
            let installed = pio_block.install(&program_data.program).unwrap();
            let (mut sm, _, mut tx) = PIOBuilder::from_installed_program(installed)
                // 8 RGB pins (chain A's 6 + 2 padding), base = rgb[0] = GP0.
                .out_pins(pins.rgb[0].id().num, 8)
                .side_set_pin_base(pins.clk.id().num)
                .clock_divisor_fixed_point(data_clk_div.0, data_clk_div.1)
                .out_shift_direction(ShiftDirection::Right)
                .autopull(true)
                .buffers(Buffers::OnlyTx)
                .build(data_sm);
            // The 8 driven RGB pins + CLK as outputs.
            let mut pindirs = [(0u8, PinDir::Output); 9];
            for i in 0..8 {
                pindirs[i] = (pins.rgb[i].id().num, PinDir::Output);
            }
            pindirs[8] = (pins.clk.id().num, PinDir::Output);
            sm.set_pindirs(pindirs);
            tx.write((W - 1).try_into().unwrap());
            (sm, tx)
        };

        let row_sm = {
            // Row program — IDENTICAL to lib.rs (address pins, latch, IRQs).
            let program_data = pio::pio_asm!(
                ".side_set 1",
                "pull           side 0b0",
                "out isr, 32    side 0b0",
                "pull           side 0b0",
                ".wrap_target",
                "mov x, isr     side 0b0",
                "addr:",
                "mov pins, ~x   side 0b0",
                "mov y, osr     side 0b0",
                "row:",
                "wait 1 irq 4   side 0b0",
                "nop            side 0b1",
                "irq 6          side 0b1",
                "irq 5          side 0b0",
                "wait 1 irq 7   side 0b0",
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
            let mut pindirs = [(0u8, PinDir::Output); 7];
            let mut idx = 0;
            for i in 0..ADDR_PINS {
                pindirs[idx] = (pins.addr[i].id().num, PinDir::Output);
                idx += 1;
            }
            pindirs[idx] = (pins.lat.id().num, PinDir::Output);
            sm.set_pindirs(pindirs[0..=idx].iter().copied());
            tx.write((H / 2 - 1).try_into().unwrap());
            tx.write((B - 1).try_into().unwrap());
            sm
        };

        let (oe_sm, oe_sm_tx) = {
            // OE / BCM engine — IDENTICAL to lib.rs.
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

        let (fb_ch, fb_loop_ch, oe_ch, oe_loop_ch) = dma_chs;

        buffer.fbptr[0] = buffer.fb0.as_ptr() as u32;
        buffer.delaysptr[0] = buffer.delays.as_ptr() as u32;

        // Number of 32-bit words the framebuffer occupies (four u8 cells/word).
        let fb_words = (fb_cells(W, H, B) * core::mem::size_of::<u8>() / 4) as u32;

        // --- DMA setup: IDENTICAL to lib.rs (channel chain is cell-size-agnostic) ---
        fb_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w.incr_read()
                .bit(true)
                .incr_write()
                .bit(false)
                .data_size()
                .size_word()
                .treq_sel()
                .bits(data_sm_tx.dreq_value())
                .irq_quiet()
                .bit(!benchmark)
                .chain_to()
                .bits(CH1::id())
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

        fb_loop_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w.incr_read()
                .bit(false)
                .incr_write()
                .bit(false)
                .data_size()
                .size_word()
                .treq_sel()
                .permanent()
                .irq_quiet()
                .bit(true)
                .chain_to()
                .bits(CH0::id())
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

        oe_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w.incr_read()
                .bit(true)
                .incr_write()
                .bit(false)
                .data_size()
                .size_word()
                .treq_sel()
                .bits(oe_sm_tx.dreq_value())
                .irq_quiet()
                .bit(true)
                .chain_to()
                .bits(CH3::id())
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

        oe_loop_ch.regs().ch_al1_ctrl().write(|w| unsafe {
            w.incr_read()
                .bit(false)
                .incr_write()
                .bit(false)
                .data_size()
                .size_word()
                .treq_sel()
                .permanent()
                .irq_quiet()
                .bit(true)
                .chain_to()
                .bits(CH2::id())
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

        Display1 {
            mem: buffer,
            fb_loop_ch,
            benchmark,
            lut,
        }
    }

    /// Swaps to the freshly-filled inactive buffer (no wait, no clear). Same
    /// rationale as `lib.rs::Display::flip` — safe for the full-frame SPI-RX path.
    pub fn flip(&mut self) {
        self.mem.fbptr[0] = if self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32) {
            self.mem.fb1.as_ptr() as u32
        } else {
            self.mem.fb0.as_ptr() as u32
        };
    }

    /// Raw pointer to the **inactive** framebuffer for the SPI-RX DMA to fill.
    /// `fb_cells(W,H,B)` bytes; must match `hub75.py::pack_single`. Recompute
    /// after every `flip()`.
    pub fn inactive_fb_ptr(&mut self) -> *mut u8 {
        if self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32) {
            self.mem.fb1.as_mut_ptr()
        } else {
            self.mem.fb0.as_mut_ptr()
        }
    }

    /// Scales the BCM/OE intervals by `gain` (brightness on a clock-bound wall).
    /// See `lib.rs::Display::set_oe_gain`.
    pub fn set_oe_gain(&mut self, gain: u32) {
        for i in 0..B {
            self.mem.delays[i] = ((1u32 << i) - 1) * gain;
        }
    }

    /// Bulk frame repack — single-chain `u8` analogue of `lib.rs::Display::render`
    /// (no chain dimension; this IS the byte-layout reference for `pack_single`).
    /// Calls `f(x, y)` for every wall pixel (`W` × `H`).
    pub fn render<F>(&mut self, mut f: F)
    where
        F: FnMut(usize, usize) -> C,
    {
        let active_is_fb0 = self.mem.fbptr[0] == (self.mem.fb0.as_ptr() as u32);
        let fb: &mut [u8] = if active_is_fb0 {
            &mut self.mem.fb1
        } else {
            &mut self.mem.fb0
        };
        let lut = self.lut;

        for y in 0..H {
            let yc = H - 1 - y; // panel-mount inversion (matches lib.rs)
            let half = yc > (H / 2) - 1;
            let shift = if half { 3 } else { 0 };
            let mask = !(0b111u8 << shift);
            let row_base = (yc % (H / 2)) * W * B;

            for x in 0..W {
                let (c_r, c_g, c_b) = lut.lookup(f(x, y));
                let base = (W - 1 - x) + row_base;
                for b in 0..B {
                    let rgb3 = (((c_b >> b) & 1) << 2
                        | ((c_g >> b) & 1) << 1
                        | ((c_r >> b) & 1)) as u8;
                    let packed = rgb3 << shift;
                    let idx = base + b * W;
                    fb[idx] = (fb[idx] & mask) | packed;
                }
            }
        }
    }
}
