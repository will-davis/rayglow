//! Phase 3 (single-chain depth test) — one chain of **4 daisy-chained 64×32
//! panels = 256×32**.
//!
//! Ordering note: the plan numbers two-chain *widening* as Phase 2 and the
//! 4-deep full wall as Phase 3. We run the single-chain depth test *first*
//! because the row is already assembled and it retires the project's #1
//! empirical risk early — **signal integrity clocking 256 px wide through four
//! panels' backplanes** (PROJECT-PLAN §4, §11.7) — with only a width-constant
//! change to the verified Phase 1 firmware. Daisy-chaining adds *panels*, not
//! pins: the 6 RGB + 4 address + CLK/LAT/OE are identical to Phase 1; the data
//! just shifts through 256 cells per half-row instead of 64.
//!
//! Run:
//!     cargo run --bin phase3-row
//!
//! Wiring: same GPIO map as Phase 1 (§6 chain A) into the FIRST panel's HUB75
//! input; chain its output → next panel input ×4. Power each panel's own 5 V
//! lugs from the dedicated supply (one 150 W/30 A unit comfortably runs 4
//! panels, §9.3); common all grounds.
//!
//! ## Tuning the pixel clock (the point of this test)
//! Start at the kjagiello default and watch the blue→white edge / right side
//! for smearing or ghosting (the faint cyan canary from Phase 1, now under 4×
//! worse conditions). If it ghosts, raise `DATA_CLK_DIV.0` (4 → ~18.75 MHz,
//! 6 → ~12.5 MHz) until clean. The refresh budget easily absorbs a slower clock
//! (§4): 256-wide @ 8-bit still clears the 150 Hz flicker floor with margin.

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
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 3 single-chain row 256x32"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// 4 daisy-chained 64x32 panels = 256 wide, 32 tall, 1/16 scan, 8-bit depth.
const W: usize = 256;
const H: usize = 32;
const B: usize = 8;

// Pixel-clock divisor — the knob for §11.7. (2,0) = ~37.5 MHz (Phase 1 value,
// worst case for SI). Raise the integer part if the right side ghosts.
const DATA_CLK_DIV: (u16, u8) = (2, 0);

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
                // 12 consecutive RGB pins. This single-chain row uses chain A
                // (GP0–5); chain B (GP6–11) is driven black, unconnected.
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

    info!(
        "phase3-row: 256x32 single chain, clkdiv ({},{}). drawing test pattern.",
        DATA_CLK_DIV.0, DATA_CLK_DIV.1
    );

    draw_test_pattern(&mut display);
    display.commit();

    let mut n = 0u32;
    loop {
        cortex_m::asm::delay(150_000_000);
        info!("alive {} (256-wide chain, zero CPU)", n);
        n = n.wrapping_add(1);
    }
}

/// Same orientation-revealing pattern as Phase 1, scaled to the full width:
/// red / green / blue vertical thirds across 256 px, a white 1px border, and a
/// white 4×4 corner marker. With four panels chained, the thirds and the single
/// continuous border immediately reveal panel order, seams, and any per-panel
/// offset or mirroring.
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

    let _ = Rectangle::new(Point::new(0, 0), Size::new(W as u32, h))
        .into_styled(
            PrimitiveStyleBuilder::new()
                .stroke_color(Rgb888::WHITE)
                .stroke_width(1)
                .build(),
        )
        .draw(d);

    let _ = Rectangle::new(Point::new(2, 2), Size::new(4, 4))
        .into_styled(fill(Rgb888::WHITE))
        .draw(d);
}
