//! Phase 1 — single chain, single 64×32 panel, static test pattern.
//!
//! Goal (PROJECT-PLAN §8, Phase 1): prove the RP2040→RP2350 port of kjagiello's
//! zero-CPU 3-SM/4-DMA engine *unchanged in structure*, lighting one panel with
//! a gamma-corrected static image. Once the engine starts, the PIO + DMA refresh
//! the panel forever with **no CPU involvement** — `main` just heartbeats.
//!
//! Run (CMSIS-DAP debugprobe attached):
//!     cargo run --bin phase1-panel
//!
//! ## Wiring (PROJECT-PLAN §6, chain A) — 3.3 V direct for first bring-up
//! | HUB75 | RP2350 GPIO | note |
//! |-------|-------------|------|
//! | R1 G1 B1 R2 G2 B2 | GP0 GP1 GP2 GP3 GP4 GP5 | consecutive (PIO `out pins`) |
//! | A B C D           | GP12 GP13 GP14 GP15     | 1/16 scan, 4 address lines   |
//! | CLK               | GP16                    | data SM sideset              |
//! | LAT               | GP17                    | row SM sideset               |
//! | OE                | GP18                    | OE SM sideset                |
//!
//! Panel 5 V + GND from a dedicated 5 V supply (NOT the dev board). Common all
//! grounds. HUB75 logic is nominally 5 V; we try 3.3 V direct first (works on
//! many panels at short range) before adding the SN74AHCT245 level shifters.

#![no_std]
#![no_main]
#![feature(generic_const_exprs)]
#![allow(incomplete_features, static_mut_refs)]

use defmt::info;
use defmt_rtt as _;
use panic_probe as _;

use rp235x_hal as hal;

use embedded_graphics::{
    pixelcolor::Rgb888,
    prelude::*,
    primitives::{PrimitiveStyle, PrimitiveStyleBuilder, Rectangle},
};

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
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 1 single panel"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// Display geometry. Start at 8-bit depth (PROJECT-PLAN §2), designed to scale.
const W: usize = 64;
const H: usize = 32;
const B: usize = 8;

// Framebuffers live in a single static so they have a 'static address the DMA
// engine can chase. `static mut` + a one-time `&mut` borrow at init is the
// pattern kjagiello uses; safe here because it is touched exactly once.
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

    // PIO0 — the engine uses 3 of its 4 state machines. Moving `pac.PIO0` while
    // borrowing `pac.RESETS` is a disjoint field access the borrow checker allows.
    let (mut pio, sm0, sm1, sm2, _) = pac.PIO0.split(&mut pac.RESETS);

    // The engine drives the DMA registers directly (bypassing the HAL DMA
    // driver), so bring the DMA block out of reset ourselves first.
    pac.RESETS.reset().modify(|_, w| w.dma().set_bit());
    pac.RESETS.reset().modify(|_, w| w.dma().clear_bit());
    while pac.RESETS.reset_done().read().dma().bit_is_clear() {}
    let dma = pac.DMA.split();

    // CIE-ish gamma LUT (matches the original pipeline's color quality).
    let lut = {
        let lut: GammaLut<B, Rgb888, _> = GammaLut::new();
        lut.init((2.1, 2.1, 2.1))
    };

    let mut display = unsafe {
        hub75::Display::new(
            &mut DISPLAY_BUFFER,
            hub75::DisplayPins {
                // 12 consecutive RGB pins. Chain A = GP0–5 (the single panel
                // here); chain B = GP6–11 (driven black, unconnected).
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
            false,  // not benchmarking
            (2, 0), // pixel clock divisor (kjagiello default ~37.5 MHz @ 150 MHz)
            &lut,
        )
    };

    info!("phase1-panel: engine running. drawing static test pattern.");

    // Draw onto the inactive buffer, then commit() flips it in. For a *static*
    // image one draw + one commit is enough — we never commit again, so the DMA
    // engine keeps scanning out the committed buffer forever.
    draw_test_pattern(&mut display);
    display.commit();

    let mut n = 0u32;
    loop {
        // Refresh is 100% PIO+DMA — the CPU is free. Heartbeat over RTT so we
        // can confirm core 0 is idle/responsive while the panel stays lit.
        cortex_m::asm::delay(150_000_000); // ~1 s @ 150 MHz
        info!("alive {} (engine refreshing with zero CPU)", n);
        n = n.wrapping_add(1);
    }
}

/// A static pattern that exercises each color channel and reveals orientation:
/// red / green / blue vertical thirds, a white 1px border, and a small white
/// square marking the top-left corner.
fn draw_test_pattern<D>(d: &mut D)
where
    D: DrawTarget<Color = Rgb888>,
{
    let _ = d.clear(Rgb888::BLACK);

    let third = (W / 3) as i32;
    let h = H as u32;
    let fill = |c| PrimitiveStyle::with_fill(c);

    let _ = Rectangle::new(Point::new(0, 0), Size::new(third as u32, h))
        .into_styled(fill(Rgb888::RED))
        .draw(d);
    let _ = Rectangle::new(Point::new(third, 0), Size::new(third as u32, h))
        .into_styled(fill(Rgb888::GREEN))
        .draw(d);
    let _ = Rectangle::new(Point::new(2 * third, 0), Size::new((W as i32 - 2 * third) as u32, h))
        .into_styled(fill(Rgb888::BLUE))
        .draw(d);

    // White 1px border around the whole panel.
    let _ = Rectangle::new(Point::new(0, 0), Size::new(W as u32, h))
        .into_styled(
            PrimitiveStyleBuilder::new()
                .stroke_color(Rgb888::WHITE)
                .stroke_width(1)
                .build(),
        )
        .draw(d);

    // Orientation marker: white 4×4 square just inside the top-left corner.
    let _ = Rectangle::new(Point::new(2, 2), Size::new(4, 4))
        .into_styled(fill(Rgb888::WHITE))
        .draw(d);
}
