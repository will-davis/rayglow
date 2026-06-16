//! Phase EXPERIMENTAL — full 256×64 wall on a **single HUB75 chain** of eight
//! 64×32 panels, SPI-fed (PROJECT-PLAN §8 deviation; not a numbered phase).
//!
//! ## Why this exists
//! The production wall is two parallel chains of four panels each, driven through
//! the custom level-shifting HAT (still in fab). While that PCB ships, this binary
//! lights the *whole* wall through the **one Adafruit RGB Matrix HAT** on hand,
//! used purely as a 3.3→5 V buffer (its 74AHCT245, DIR strapped Pi→panel). All
//! eight panels daisy-chain into ONE electrical chain in a U/serpentine:
//!
//! ```text
//!   in →[TR]→[T ]→[T ]→[TL]        top row, signal travels right→left
//!                          │        U-turn
//!        [BL]→[B ]→[B ]→[BR]→ out  bottom row, 180°-rotated, travels left→right
//! ```
//!
//! ## How it maps onto the (unchanged) two-chain engine
//! Electrically eight 64-wide panels in series is a **512×32** strip. We drive it
//! as the engine's **chain A only** (`W = 512`, `H = 32`): wire GP0–5 (R1G1B1
//! R2G2B2) into the HAT; **GP6–11 (chain B) stay unconnected and output black.**
//! This is the `phase3-row` degenerate single-chain case (lib.rs §"single-chain
//! setup"), widened from 256 to 512, with Phase 4's `set_oe_gain` and Phase 5's
//! zero-CPU SPI ingest folded in. The engine is unchanged and already
//! hardware-verified — nothing here is new firmware structure, only geometry.
//!
//! ## The cost (read before wiring)
//!   * **128 KB frame, not 64 KB.** The Phase-2 `u16` cell reserves 6 bits for the
//!     idle chain B, so `fb_cells(512,32,8)*2 = 131072`. Half the SPI payload is
//!     zeros. The rpi5 `spidev` bufsiz is already 131072 (one transfer). A future
//!     `u8` single-chain cell path would reclaim this; not worth it for bring-up.
//!   * **~½ the two-chain refresh**, because chain A now shifts 512 px/row instead
//!     of 256 with no parallel chain B to hide behind. Drop `B` to 7/6 if it
//!     flickers below the ~150 Hz floor.
//!   * **Signal integrity:** 8 panels in series is 2× the depth `phase3-row`
//!     verified clean (4 panels @ 37.5 MHz). Start SLOW — `(6,0)` ≈ 12.5 MHz —
//!     and only ramp once the HAT's '245 buffers + the wall behave. The HAT also
//!     puts a small RC on CLK (Adafruit's anti-ghosting fix for the Pi); with the
//!     RP2350's cleaner/faster edges that RC argues for the slow clock too.
//!
//! ## Wiring — RP2350 GP → Adafruit HAT (adafruit-hat pinout; confirm against
//! hzeller `lib/hardware-mapping.c` and your HAT revision before soldering):
//!     GP0 R1  GP1 G1  GP2 B1  GP3 R2  GP4 G2  GP5 B2   (chain A; GP6–11 unused)
//!     GP12 A  GP13 B  GP14 C  GP15 D   (1:16 scan, 64×32 panels — no E line)
//!     GP16 CLK  GP17 LAT  GP18 OE
//! Common all grounds; power panels from the bench 5 V lugs, NOT the HAT terminal.
//! SPI link pins are unchanged from Phase 5 (see below).
//!
//! The rpi5 must pack for this geometry: single-chain serpentine fold (256×64 →
//! 512×32 electrical, bottom row 180°-rotated) into chain A, chain B left black.
//! Keep `rayglow/render/hub75.py` + `tools/verify.py` in lockstep with `W=512`.
//!
//! Run:
//!     cargo run --bin phase-experimental

#![no_std]
#![no_main]
#![feature(generic_const_exprs)]
#![allow(incomplete_features, static_mut_refs)]

use defmt::info;
use defmt_rtt as _;
use panic_probe as _;

use rp235x_hal as hal;

use embedded_graphics::pixelcolor::Rgb888;
use embedded_hal::digital::OutputPin;

use hal::gpio::{FunctionPio1, PullDown, PullUp};
use hal::pio::{Buffers, PIOBuilder, PIOExt, PinDir, ShiftDirection};

use rp2350_rgb_driver as hub75;
use hub75::dma::{Channel, ChannelIndex, ChannelRegs, DMAExt, CH4};
use hub75::lut::GammaLut;

#[link_section = ".start_block"]
#[used]
pub static IMAGE_DEF: hal::block::ImageDef = hal::block::ImageDef::secure_exe();

#[link_section = ".bi_entries"]
#[used]
pub static PICOTOOL_ENTRIES: [hal::binary_info::EntryAddr; 3] = [
    hal::binary_info::rp_cargo_bin_name!(),
    hal::binary_info::rp_cargo_version!(),
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase EXPERIMENTAL single-chain 512x32"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// Single chain of EIGHT 64×32 panels = 512 px wide × 32 tall electrical strip,
// driven on chain A only (GP0–5). Chain B (GP6–11) is idle/black. Must match the
// host packer's single-chain geometry (rayglow/render/hub75.py, W=512).
const W: usize = 512;
const H: usize = 32;
const B: usize = 8;
// HUB75 pixel clock = sys_clk / (2*div). 8 panels in series is 2× the depth
// phase3-row verified clean at (2,0)=37.5 MHz, AND the Adafruit HAT adds an RC on
// CLK — so start SLOW and ramp only after the wall behaves. (6,0)=12.5 MHz.
const DATA_CLK_DIV: (u16, u8) = (6, 0); // ~12.5 MHz pixel clock (SI-safe to start)
// Brightness gain (Phase 4 §set_oe_gain). 512-wide doubles the per-plane shift
// window vs 256, so there is MORE dead time to fill — the gain ceiling roughly
// doubles too (~16 before trading refresh). Start near the Phase-5 value, tune.
const OE_GAIN: u32 = 24;

// Framebuffer size in BYTES = one SPI frame. fb_cells is u16 count → ×2.
// fb_cells(512,32,8) = 65536 → 131072 bytes (128 KB; half is the idle chain B).
const FRAME_BYTES: u32 = (hub75::fb_cells(W, H, B) * 2) as u32; // 131072

// SPI-link GPIO. Unchanged from Phase 5. MOSI is the PIO IN base; SCLK and CS are
// sampled with `wait gpio` (absolute), so they are hardcoded in the PIO program
// below — keep these consts in sync with the literals there.
const MOSI_PIN: u8 = 20;
const SCLK_PIN: u8 = 21;
const CS_PIN: u8 = 22; // chip-select (CE0), active low — frame boundary
const READY_PIN: u8 = 12;
const _: () = assert!(SCLK_PIN == 21, "PIO `wait gpio 21` must match SCLK_PIN");
const _: () = assert!(CS_PIN == 22, "PIO `wait gpio 22` must match CS_PIN");

static mut DISPLAY_BUFFER: hub75::DisplayMemory<W, H, B> = hub75::DisplayMemory::new();

#[hal::entry]
fn main() -> ! {
    let mut pac = hal::pac::Peripherals::take().unwrap();

    let mut watchdog = hal::watchdog::Watchdog::new(pac.WATCHDOG);
    let clocks = hal::clocks::init_clocks_and_plls(
        XTAL_FREQ_HZ,
        pac.XOSC,
        pac.CLOCKS,
        pac.PLL_SYS,
        pac.PLL_USB,
        &mut pac.RESETS,
        &mut watchdog,
    )
    .ok()
    .unwrap();

    let timer = hal::Timer::new_timer0(pac.TIMER0, &mut pac.RESETS, &clocks);

    let sio = hal::Sio::new(pac.SIO);
    let pins = hal::gpio::Pins::new(
        pac.IO_BANK0,
        pac.PADS_BANK0,
        sio.gpio_bank0,
        &mut pac.RESETS,
    );

    // PIO0 → HUB75 scan-out engine (unchanged from earlier phases).
    let (mut pio0, sm0, sm1, sm2, _) = pac.PIO0.split(&mut pac.RESETS);
    // PIO1 → SPI receiver.
    let (mut pio1, rx_sm, _, _, _) = pac.PIO1.split(&mut pac.RESETS);

    // DMA: ch0–3 for the engine, ch4 for SPI-RX → framebuffer.
    pac.RESETS.reset().modify(|_, w| w.dma().set_bit());
    pac.RESETS.reset().modify(|_, w| w.dma().clear_bit());
    while pac.RESETS.reset_done().read().dma().bit_is_clear() {}
    let dma = pac.DMA.split();

    let lut = {
        let lut: GammaLut<B, Rgb888, _> = GammaLut::new();
        lut.init((2.1, 2.1, 2.1))
    };

    let mut display = unsafe {
        hub75::Display::new(
            &mut DISPLAY_BUFFER,
            hub75::DisplayPins {
                // Chain A (GP0–5) → the single physical chain via the Adafruit HAT.
                // Chain B (GP6–11) is bound but UNCONNECTED — outputs black.
                rgb: [
                    pins.gpio0.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio1.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio2.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio3.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio4.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio5.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio6.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio7.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio8.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio9.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio10.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio11.into_function().into_pull_type().into_dyn_pin(),
                ],
                addr: [
                    pins.gpio12.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio13.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio14.into_function().into_pull_type().into_dyn_pin(),
                    pins.gpio15.into_function().into_pull_type().into_dyn_pin(),
                ],
                clk: pins.gpio16.into_function().into_pull_type().into_dyn_pin(),
                lat: pins.gpio17.into_function().into_pull_type().into_dyn_pin(),
                oe: pins.gpio18.into_function().into_pull_type().into_dyn_pin(),
            },
            &mut pio0,
            (sm0, sm1, sm2),
            (dma.ch0, dma.ch1, dma.ch2, dma.ch3),
            false,
            DATA_CLK_DIV,
            &lut,
        )
    };
    display.set_oe_gain(OE_GAIN);

    // --- SPI-RX pin setup (unchanged from Phase 5) ------------------------
    // MOSI + SCLK into PIO1 with pull-down (E9 backstop). CS is active-low, so it
    // gets a pull-UP (idles high between frames).
    let _mosi = pins
        .gpio20
        .into_function::<FunctionPio1>()
        .into_pull_type::<PullDown>();
    let _sclk = pins
        .gpio21
        .into_function::<FunctionPio1>()
        .into_pull_type::<PullDown>();
    let _cs = pins
        .gpio22
        .into_function::<FunctionPio1>()
        .into_pull_type::<PullUp>();
    // READY is a plain push-pull output (SIO), idle low until a frame is armed.
    let mut ready = pins.gpio26.into_push_pull_output();
    let _ = ready.set_low();

    // --- SPI-RX PIO program (mode 0, CS-framed) — unchanged from Phase 5 ----
    let program = pio::pio_asm!(
        ".wrap_target",
        "wait 1 gpio 22", // CS high  — idle / previous frame ended
        "wait 0 gpio 22", // CS low   — fresh frame start, shift counter = 0
        "bitloop:",
        "wait 1 gpio 21", // SCLK rising = sample point
        "in pins, 1",     // sample MOSI (IN base = GP20)
        "wait 0 gpio 21", // SCLK falling
        "jmp bitloop",    // next bit (restart() re-parks at the CS preamble)
        ".wrap",
    );
    let installed = pio1.install(&program.program).unwrap();
    let (mut rx_sm, rx_fifo, _tx) = PIOBuilder::from_installed_program(installed)
        .in_pin_base(MOSI_PIN)
        .in_shift_direction(ShiftDirection::Left) // MSB first
        .autopush(true)
        .push_threshold(8) // one byte per push
        .buffers(Buffers::OnlyRx)
        .clock_divisor_fixed_point(1, 0) // full system clock
        .build(rx_sm);
    rx_sm.set_pindirs([
        (MOSI_PIN, PinDir::Input),
        (SCLK_PIN, PinDir::Input),
        (CS_PIN, PinDir::Input),
    ]);

    let rx_ch = dma.ch4;
    let fifo_addr = rx_fifo.fifo_address() as u32;
    let rx_dreq = rx_fifo.dreq_value();

    info!(
        "phase-experimental: {}x{} SINGLE-CHAIN wall (chain A only). CS-framed SPI-RX on PIO1 (MOSI GP{}, SCLK GP{}, CS GP{}), READY GP{}. frame = {} bytes (128 KB; half is idle chain B).",
        W,
        2 * H,
        MOSI_PIN,
        SCLK_PIN,
        CS_PIN,
        READY_PIN,
        FRAME_BYTES
    );

    let mut frames: u32 = 0;
    let mut last_us: u32 = timer.get_counter_low();
    let mut sm = rx_sm.start();

    loop {
        // Destination = the buffer not currently on screen. Recompute every
        // frame (commit swaps the roles).
        let dst = display.inactive_fb_ptr() as u32;

        // Fresh alignment: drain any stale RX byte and restart the SM so its
        // shift counter is 0 → the next SCLK edge is bit 0 of this frame. Then
        // arm the DMA to drain the FIFO into `dst`.
        sm.clear_fifos();
        sm.restart();
        arm_rx_dma(&rx_ch, fifo_addr, dst, FRAME_BYTES, rx_dreq);

        // Tell the rpi5 we're ready to receive this frame.
        let _ = ready.set_high();

        // Zero-CPU ingest: spin until the DMA has placed all FRAME_BYTES.
        while rx_busy(&rx_ch) {}

        let _ = ready.set_low();

        // Flip the freshly-received frame onto the wall.
        display.commit();

        frames += 1;
        let now = timer.get_counter_low();
        if now.wrapping_sub(last_us) >= 1_000_000 {
            info!("rx fps {}", frames);
            frames = 0;
            last_us = now;
        }
    }
}

/// (Re)arms the SPI-RX DMA channel for one frame: FIFO → framebuffer, byte-size,
/// write-incrementing, paced by the PIO RX DREQ. Writing the trigger alias for
/// the write address starts the channel.
fn arm_rx_dma(ch: &Channel<CH4>, fifo: u32, dst: u32, count: u32, dreq: u8) {
    ch.regs().ch_al1_ctrl().write(|w| unsafe {
        w.incr_read()
            .bit(false) // FIFO address is fixed
            .incr_write()
            .bit(true) // walk through the framebuffer
            .data_size()
            .size_byte() // one byte per beat → in-order bytes
            .treq_sel()
            .bits(dreq) // paced by PIO1 RX
            .irq_quiet()
            .bit(true) // we poll, no IRQ
            .chain_to()
            .bits(CH4::id()) // chain to self = no chaining
            .en()
            .bit(true)
    });
    ch.regs().ch_read_addr().write(|w| unsafe { w.bits(fifo) });
    ch.regs().ch_trans_count().write(|w| unsafe { w.bits(count) });
    // Trigger: writing the write-address (trig alias) starts the transfer.
    ch.regs()
        .ch_al2_write_addr_trig()
        .write(|w| unsafe { w.bits(dst) });
}

fn rx_busy(ch: &Channel<CH4>) -> bool {
    ch.regs().ch_ctrl_trig().read().busy().bit_is_set()
}
