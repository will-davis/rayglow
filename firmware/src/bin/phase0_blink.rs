//! Phase 0 — RP2350 bring-up.
//!
//! Goal (PROJECT-PLAN §8, Phase 0): prove the toolchain, the RP2350 boot
//! `IMAGE_DEF` block, the flash/boot path, and `defmt` RTT logging — *before*
//! any PIO/DMA work. Done when: `defmt` "blink N" logs stream over the probe
//! (and, if an external LED is wired to GP25, it blinks at 1 Hz).
//!
//! Run (with a CMSIS-DAP debugprobe attached):
//!     cargo run --bin phase0-blink
//!
//! Board: **Waveshare RP2350-PiZero (RP2350B)** — the project's main board.
//! Unlike a Pico 2, this board has *no user-controllable LED* (only a hardwired
//! power LED), so the visible "blink" needs an external LED: wire one (with a
//! ~330 Ohm series resistor) from the LED pin (GP25) to GND, or just put a
//! scope/logic probe on GP25. The real liveness proof for Phase 0 is the
//! `defmt` counter over RTT — that's what confirms the boot block + flash + log
//! path.
//!
//! GP25 is chosen because it is free in the PROJECT-PLAN §6 pin map (HUB75 uses
//! GP0-18, chains C/D reserve GP19-21), so this bring-up wiring won't collide
//! with later phases.

#![no_std]
#![no_main]

use defmt::info;
use defmt_rtt as _; // global defmt logger over RTT
use panic_probe as _; // panic handler -> defmt -> probe

use embedded_hal::delay::DelayNs;
use embedded_hal::digital::OutputPin;
use hal::clocks::Clock as _; // brings `.freq()` into scope on the clock handles
use rp235x_hal as hal;

/// RP2350 boot image header. The Boot ROM scans the first 4 KiB of flash for
/// this block (placed in `.start_block` by `memory.x`) to validate and launch
/// the image. This replaces the RP2040's `boot2` second-stage bootloader.
#[link_section = ".start_block"]
#[used]
pub static IMAGE_DEF: hal::block::ImageDef = hal::block::ImageDef::secure_exe();

/// External crystal on the Pico 2 / RP2350-PiZero is 12 MHz.
const XTAL_FREQ_HZ: u32 = 12_000_000;

/// picotool 'Binary Info' — surfaces a name/description when inspecting the
/// flashed image with `picotool info`. Optional, but cheap and handy.
#[link_section = ".bi_entries"]
#[used]
pub static PICOTOOL_ENTRIES: [hal::binary_info::EntryAddr; 3] = [
    hal::binary_info::rp_cargo_bin_name!(),
    hal::binary_info::rp_cargo_version!(),
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 0 bring-up"),
];

#[hal::entry]
fn main() -> ! {
    let mut pac = hal::pac::Peripherals::take().unwrap();

    // Clocks: bring up XOSC + PLLs to the default 150 MHz system clock. The
    // watchdog drives the clock-init handshake (RP-series quirk), so it must be
    // constructed first even though we don't otherwise use it here.
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

    // Microsecond timer used as a blocking delay source.
    let mut timer = hal::Timer::new_timer0(pac.TIMER0, &mut pac.RESETS, &clocks);

    // GPIO.
    let sio = hal::Sio::new(pac.SIO);
    let pins = hal::gpio::Pins::new(
        pac.IO_BANK0,
        pac.PADS_BANK0,
        sio.gpio_bank0,
        &mut pac.RESETS,
    );
    let mut led = pins.gpio25.into_push_pull_output();

    info!("phase0-blink: RP2350 up. sys_clk = {} Hz", clocks.system_clock.freq().to_Hz());

    let mut count: u32 = 0;
    loop {
        led.set_high().unwrap();
        timer.delay_ms(500);
        led.set_low().unwrap();
        timer.delay_ms(500);

        count = count.wrapping_add(1);
        info!("blink {}", count);
    }
}
