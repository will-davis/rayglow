//! QC test — standalone single-panel dead-pixel hunter.
//!
//! A deliberately *dumb* sibling of `phase1-panel`: same engine, same chain-A
//! wiring, but instead of one static image it cycles a fixed set of full-screen
//! QC patterns forever so you can eyeball / reflow dead pixels on a single
//! 64×32 panel without the rest of the pipeline (no Pi, no SPI, no level
//! shifter). Reuses the hardware-verified Phase 1 init verbatim.
//!
//! Run (CMSIS-DAP debugprobe attached to the spare RP2350B):
//!     cargo run --bin qc-test
//!
//! ## Wiring — identical to phase1-panel, chain A, 3.3 V direct (no 74AHCT245)
//! | HUB75 | RP2350 GPIO | note |
//! |-------|-------------|------|
//! | R1 G1 B1 R2 G2 B2 | GP0 GP1 GP2 GP3 GP4 GP5 | consecutive (PIO `out pins`) |
//! | A B C D           | GP12 GP13 GP14 GP15     | 1/16 scan, 4 address lines   |
//! | CLK               | GP16                    | data SM sideset              |
//! | LAT               | GP17                    | row SM sideset               |
//! | OE                | GP18                    | OE SM sideset                |
//!
//! Panel 5 V + GND from a dedicated supply (NOT the dev board). Common all
//! grounds. The data/control logic is driven at 3.3 V — fine at short range on
//! the panels this project uses (already proven on this exact board).
//!
//! ## Patterns (each held PATTERN_MS, then advance; loops forever)
//! 0. all WHITE   — every subpixel on; spots fully-dead pixels + tints
//! 1. all RED     — isolate the red channel/column drivers
//! 2. all GREEN   — isolate the green channel
//! 3. all BLUE    — isolate the blue channel
//! 4. CHECKER 1px — white/black per pixel; catches stuck-on + neighbour shorts
//! 5. V-STRIPES   — alternating columns white/black; column-driver / x-short test
//! 6. H-STRIPES   — alternating rows white/black; row-address / y-short test

#![no_std]
#![no_main]
#![feature(generic_const_exprs)]
#![allow(incomplete_features, static_mut_refs)]

use defmt::info;
use defmt_rtt as _;
use panic_probe as _;

use rp235x_hal as hal;

use embedded_graphics::pixelcolor::Rgb888;
use embedded_graphics::prelude::RgbColor;

use hal::pio::PIOExt;
use rp2350_rgb_driver as hub75;
use hub75::dma::DMAExt;
use hub75::lut::GammaLut;

#[link_section = ".start_block"]
#[used]
pub static IMAGE_DEF: hal::block::ImageDef = hal::block::ImageDef::secure_exe();

#[link_section = ".bi_entries"]
#[used]
pub static PICOTOOL_ENTRIES: [hal::binary_info::EntryAddr; 3] = [
    hal::binary_info::rp_cargo_bin_name!(),
    hal::binary_info::rp_cargo_version!(),
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - QC single-panel test"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// One 64×32 panel on chain A (chain B / GP6–11 idle & unconnected).
const W: usize = 64;
const H: usize = 32;
const B: usize = 8;

// (2,0) ≈ 37.5 MHz pixel clock @ 150 MHz sys — the project's validated default.
const DATA_CLK_DIV: (u16, u8) = (2, 0);

// BCM on-time gain (brightness). 1 = native (what phase1 used). A narrow 64-px
// panel has plenty of on-time, but bump it for a brighter, easier-to-inspect
// image during reflow. Safe up to ~8 (see Display::set_oe_gain).
const OE_GAIN: u32 = 2;

// Hold time per pattern. ~150 MHz core → asm::delay(150_000_000) ≈ 1 s.
const HOLD_CYCLES: u32 = 225_000_000; // ~1.5 s

const NUM_PATTERNS: u32 = 7;

static mut DISPLAY_BUFFER: hub75::DisplayMemory<W, H, B> = hub75::DisplayMemory::new();

#[hal::entry]
fn main() -> ! {
    let mut pac = hal::pac::Peripherals::take().unwrap();

    let mut watchdog = hal::watchdog::Watchdog::new(pac.WATCHDOG);
    let _clocks = hal::clocks::init_clocks_and_plls(
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

    let sio = hal::Sio::new(pac.SIO);
    let pins = hal::gpio::Pins::new(
        pac.IO_BANK0,
        pac.PADS_BANK0,
        sio.gpio_bank0,
        &mut pac.RESETS,
    );

    let (mut pio, sm0, sm1, sm2, _) = pac.PIO0.split(&mut pac.RESETS);

    // The engine pokes the DMA registers directly, so reset the block ourselves.
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
            &mut pio,
            (sm0, sm1, sm2),
            (dma.ch0, dma.ch1, dma.ch2, dma.ch3),
            false,
            DATA_CLK_DIV,
            &lut,
        )
    };

    display.set_oe_gain(OE_GAIN);

    info!("qc-test: engine running on a {}x{} panel (chain A). cycling patterns.", W, H);

    let mut pat: u32 = 0;
    loop {
        // Whole-frame repack into the inactive buffer, then flip it in. The DMA
        // engine then holds that frame on the panel with zero CPU during delay.
        display.render(|x, y| pattern(pat, x, y));
        display.commit();
        info!("pattern {} -> {}", pat, name(pat));

        cortex_m::asm::delay(HOLD_CYCLES);
        pat = (pat + 1) % NUM_PATTERNS;
    }
}

/// Color for pixel (x, y) under the given pattern index. Only y in 0..H is on a
/// real panel (chain A); the render pass also calls this for y in H..2H (the
/// idle chain B) — harmless, the alternating math is well-defined there too.
#[inline(always)]
fn pattern(pat: u32, x: usize, y: usize) -> Rgb888 {
    let w = Rgb888::WHITE;
    let k = Rgb888::BLACK;
    match pat {
        0 => w,
        1 => Rgb888::RED,
        2 => Rgb888::GREEN,
        3 => Rgb888::BLUE,
        4 => if (x + y) & 1 == 0 { w } else { k }, // checkerboard
        5 => if x & 1 == 0 { w } else { k },        // vertical 1px stripes (columns)
        _ => if y & 1 == 0 { w } else { k },        // horizontal 1px stripes (rows)
    }
}

fn name(pat: u32) -> &'static str {
    match pat {
        0 => "white",
        1 => "red",
        2 => "green",
        3 => "blue",
        4 => "checker",
        5 => "v-stripes",
        _ => "h-stripes",
    }
}
