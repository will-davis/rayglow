//! Phase 2 — two parallel chains, one 64×32 panel each (64×64 wall).
//!
//! Goal (PROJECT-PLAN §8, Phase 2): prove the data-path widening — `out pins,
//! 16`, the 12-bit `u16` framebuffer cell, both chains clocked together — by
//! lighting **two independent panels in parallel** with no change to refresh
//! rate. Chain A (top, wall rows 0–31) on GP0–5; chain B (bottom, rows 32–63)
//! on GP6–11; CLK/LAT/OE/ADDR shared.
//!
//! The two chains draw *deliberately different* patterns (vertical color bars
//! vs horizontal color bars, with mirrored corner markers) so the result
//! immediately proves the chains are independently addressed — not a mirrored
//! copy or a bleed of one into the other.
//!
//! Run:
//!     cargo run --bin phase2-dualchain
//!
//! ## Wiring (adds chain B to the Phase 1 setup)
//! | HUB75 | RP2350 GPIO |
//! |-------|-------------|
//! | Chain A R1 G1 B1 R2 G2 B2 | GP0 GP1 GP2 GP3 GP4 GP5 |  (panel 1)
//! | Chain B R1 G1 B1 R2 G2 B2 | GP6 GP7 GP8 GP9 GP10 GP11 | (panel 2)
//! | A B C D (shared)          | GP12 GP13 GP14 GP15 |
//! | CLK / LAT / OE (shared)   | GP16 / GP17 / GP18 |
//!
//! Both panels' 5 V from the dedicated supply (own lugs); common all grounds.
//! CLK/LAT/OE/ADDR fan out to *both* panels — at the bench, jumper each shared
//! signal to both connectors (the HAT will buffer these per-connector, §9).

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
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 2 two parallel chains"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// Per-chain geometry: one 64x32 panel each. Wall is W x 2H = 64x64.
const W: usize = 64;
const H: usize = 32;
const B: usize = 8;
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
                // Chain A = GP0–5 (panel 1), chain B = GP6–11 (panel 2).
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

    info!("phase2-dualchain: two parallel chains, 64x64 wall. drawing.");

    draw_test_pattern(&mut display);
    display.commit();

    let mut n = 0u32;
    loop {
        cortex_m::asm::delay(150_000_000);
        info!("alive {} (two chains, zero CPU)", n);
        n = n.wrapping_add(1);
    }
}

/// Chain A (top, rows 0–31): **vertical** R/G/B thirds + top-left marker.
/// Chain B (bottom, rows 32–63): **horizontal** R/G/B thirds + top-right marker.
/// Different layouts per chain prove independent addressing.
fn draw_test_pattern<D>(d: &mut D)
where
    D: DrawTarget<Color = Rgb888>,
{
    let _ = d.clear(Rgb888::BLACK);
    let fill = |c| PrimitiveStyle::with_fill(c);
    let border = PrimitiveStyleBuilder::new()
        .stroke_color(Rgb888::WHITE)
        .stroke_width(1)
        .build();

    // ---- Chain A: vertical thirds, rows 0..31 ----
    let third = (W / 3) as i32;
    let _ = Rectangle::new(Point::new(0, 0), Size::new(third as u32, H as u32))
        .into_styled(fill(Rgb888::RED))
        .draw(d);
    let _ = Rectangle::new(Point::new(third, 0), Size::new(third as u32, H as u32))
        .into_styled(fill(Rgb888::GREEN))
        .draw(d);
    let _ = Rectangle::new(Point::new(2 * third, 0), Size::new((W as i32 - 2 * third) as u32, H as u32))
        .into_styled(fill(Rgb888::BLUE))
        .draw(d);
    let _ = Rectangle::new(Point::new(0, 0), Size::new(W as u32, H as u32))
        .into_styled(border)
        .draw(d);
    // top-left marker
    let _ = Rectangle::new(Point::new(2, 2), Size::new(4, 4))
        .into_styled(fill(Rgb888::WHITE))
        .draw(d);

    // ---- Chain B: horizontal thirds, rows 32..63 ----
    let y0 = H as i32; // 32
    let tb = (H / 3) as i32;
    let _ = Rectangle::new(Point::new(0, y0), Size::new(W as u32, tb as u32))
        .into_styled(fill(Rgb888::RED))
        .draw(d);
    let _ = Rectangle::new(Point::new(0, y0 + tb), Size::new(W as u32, tb as u32))
        .into_styled(fill(Rgb888::GREEN))
        .draw(d);
    let _ = Rectangle::new(Point::new(0, y0 + 2 * tb), Size::new(W as u32, (H as i32 - 2 * tb) as u32))
        .into_styled(fill(Rgb888::BLUE))
        .draw(d);
    let _ = Rectangle::new(Point::new(0, y0), Size::new(W as u32, H as u32))
        .into_styled(border)
        .draw(d);
    // top-right marker (distinct corner from chain A)
    let _ = Rectangle::new(Point::new(W as i32 - 6, y0 + 2), Size::new(4, 4))
        .into_styled(fill(Rgb888::WHITE))
        .draw(d);
}
