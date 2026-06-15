//! Phase 4 — bulk frame ingest + animation, full **256×64 wall** (two chains of
//! four daisy-chained 64×32 panels each).
//!
//! Goal (PROJECT-PLAN §8, Phase 4): prove smooth ≥30 fps animation from a
//! CPU-generated source, using the **bulk frame→bit-plane repack**
//! (`Display::render`, §5.4) instead of per-pixel `set_pixel`, with
//! double-buffered `commit()` each frame. Refresh stays 100% PIO+DMA, so the
//! core is free to *generate* frames — here, a rainbow plasma.
//!
//! Scope note: the repack runs on **core 0** for now. Pinning it to core 1
//! (§5.4) only matters once core 0 must service the Phase 5 SPI link; the
//! engine itself uses zero CPU, so a single core generates frames just fine.
//!
//! Run:
//!     cargo run --bin phase4-anim
//! Watch the RTT log for the sustained `fps` readout.

#![no_std]
#![no_main]
#![feature(generic_const_exprs)]
#![allow(incomplete_features, static_mut_refs)]

use defmt::info;
use defmt_rtt as _;
use panic_probe as _;

use rp235x_hal as hal;

use embedded_graphics::pixelcolor::Rgb888;

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
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 4 animation"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// Full 256×64 wall: each chain is four daisy-chained 64×32 panels (W = 256),
// two chains stacked (2H = 64). The data SM clocks 256 px/row per chain.
const W: usize = 256;
const H: usize = 32;
const B: usize = 8;
// (2,0) ≈ 37.5 MHz pixel clock @ 150 MHz sys — validated clean over 4 chained
// panels (lib.rs Display::new docs, PROJECT-PLAN §11.7). Keep the fast clock.
const DATA_CLK_DIV: (u16, u8) = (2, 0);

// BCM on-time gain (brightness). On a 256-wide wall the per-plane pixel-shift
// (~6.8 µs) dwarfs the native LED on-time (≤0.85 µs), so the LEDs sit dark most
// of each plane. Scaling the OE intervals fills that dead shift-window —
// brightness rises ~linearly with gain while the refresh stays clock-bound up
// to ~8. Tune here and reflash. (gain 1 = native; see Display::set_oe_gain.)
const OE_GAIN: u32 = 6;

static mut DISPLAY_BUFFER: hub75::DisplayMemory<W, H, B> = hub75::DisplayMemory::new();

/// Fixed-point sine LUT: `SIN[i] = round(sin(2π·i/256) * 1024)`, range ±1024.
/// Lets the plasma run on pure integer math (no per-pixel `sinf`), which is
/// what keeps the frame rate high.
const SIN_SCALE: i32 = 1024;
static mut SIN: [i32; 256] = [0; 256];

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

    // Microsecond timer for the fps measurement.
    let timer = hal::Timer::new_timer0(pac.TIMER0, &mut pac.RESETS, &clocks);

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

    // Fill the sine LUT once.
    for (i, slot) in unsafe { SIN.iter_mut() }.enumerate() {
        let theta = (i as f32) * core::f32::consts::TAU / 256.0;
        *slot = libm::roundf(libm::sinf(theta) * SIN_SCALE as f32) as i32;
    }
    let sin = unsafe { &SIN };

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

    // Reclaim brightness lost to the wide wall's long pixel-shift time.
    display.set_oe_gain(OE_GAIN);

    info!(
        "phase4-anim: rendering plasma on a {}x{} wall, OE gain {}.",
        W,
        2 * H,
        OE_GAIN
    );

    let mut t: u32 = 0;
    let mut frames: u32 = 0;
    let mut last_us: u32 = timer.get_counter_low();

    loop {
        // Whole-frame bulk repack from the plasma source, then flip. Timed so
        // the per-second log can break fps down into work (render) vs the
        // refresh-synced buffer flip (commit).
        let a = timer.get_counter_low();
        display.render(|x, y| plasma(x, y, t, sin));
        let b = timer.get_counter_low();
        display.commit();
        let c = timer.get_counter_low();

        t = t.wrapping_add(2); // animation speed
        frames += 1;

        let now_us = timer.get_counter_low();
        if now_us.wrapping_sub(last_us) >= 1_000_000 {
            info!(
                "fps {} | render {}us commit {}us",
                frames,
                b.wrapping_sub(a),
                c.wrapping_sub(b)
            );
            frames = 0;
            last_us = now_us;
        }
    }
}

/// Rainbow plasma, all integer math via the sine LUT. Sums three traveling sine
/// waves to a scalar field, then maps it through 120°-phased sines to RGB.
#[inline(always)]
fn plasma(x: usize, y: usize, t: u32, sin: &[i32; 256]) -> Rgb888 {
    let x = x as u32;
    let y = y as u32;

    // Wavelengths tuned for a 256-wide wall: low spatial frequency so each wave
    // sweeps across the whole wall (and over the panel seams) as one continuous
    // field — ~2 cycles horizontally, ~1 vertically. A seam would show up as a
    // break/offset in these bands, so this doubles as the continuity check.
    let a = sin[((x * 2 + t) & 0xff) as usize];
    let b = sin[((y * 4 + t.wrapping_mul(3) / 2) & 0xff) as usize];
    let c = sin[(((x + y) * 2 + t.wrapping_mul(2)) & 0xff) as usize];

    // v ∈ [-3*1024, +3*1024]; map to a 0..=255 palette index.
    let v = a + b + c;
    let idx = (((v + 3 * SIN_SCALE) * 255) / (6 * SIN_SCALE)) as usize & 0xff;

    let chan = |phase: usize| -> u8 {
        // (sin + 1024) → 0..2048 → scale to 0..255
        (((sin[(idx + phase) & 0xff] + SIN_SCALE) * 255) / (2 * SIN_SCALE)) as u8
    };

    // 120° phase offsets (256/3 ≈ 85) → smooth rainbow.
    Rgb888::new(chan(0), chan(85), chan(170))
}
