//! Phase 5 — Pi 5 → RP2350 SPI link (PROJECT-PLAN §8, §10).
//!
//! The rpi5 renders frames (rayglow), packs them into the rp2350b's exact
//! bit-plane framebuffer layout (`rayglow/render/hub75.py`, proven byte-identical
//! to `Display::render`), and streams the 64 KB result over SPI. This firmware
//! receives it with **zero CPU in the ingest path** — a dedicated PIO block +
//! DMA drop the bytes straight into the inactive framebuffer — mirroring the
//! zero-CPU refresh engine on the other side. PIO0 stays the HUB75 scan-out
//! engine; **PIO1 is the SPI receiver**.
//!
//! ## Why PIO and not the hardware SPI (PL022)
//! The PL022 in *slave* mode is limited to peri_clk/12 ≈ 12.5 MHz → a 64 KB
//! frame takes ~42 ms ≈ 24 fps. Too slow. A PIO SPI-slave sampling loop runs at
//! the system clock and captures SCLK up to ~sysclk/3–4 (≈ 37–50 MHz @ 150 MHz),
//! i.e. 60–76 fps for the same frame. So we trade a peripheral for a PIO block
//! we have to spare (3 PIO blocks; only PIO0 is used by the engine).
//!
//! ## Byte-granularity ingest (why there is no endianness puzzle)
//! The receive program samples MOSI MSB-first and **autopushes every 8 bits**;
//! the DMA does **byte-size** transfers. Each wire byte therefore lands at the
//! next framebuffer address in order, so memory ends up byte-for-byte equal to
//! the packer's stream — no word assembly, no byte-swap, nothing to reconcile.
//! (A later optimization could use 32-bit words + the DMA `BSWAP` bit for fewer
//! DMA beats, but byte mode is unambiguous for bring-up.)
//!
//! ## Frame handshake (deterministic bit alignment)
//! Per frame: firmware arms the RX DMA to the current inactive buffer, restarts
//! the SM (so its shift counter is 0 → the first SCLK edge is bit 0), then
//! raises **READY**. The rpi5 waits for READY before asserting CE and clocking
//! 65536 bytes, then deasserts. The firmware polls the DMA byte count to
//! completion, lowers READY, and `commit()`s to flip the frame in. Because each
//! frame restarts the SM, there is no way for bit alignment to drift across
//! frames even if one is dropped.
//!
//! ## Wiring (rp2350b GPIO ↔ rpi5 SPI0)  — reconcile physical header pins with
//! `pcb/PIZERO-HEADER-PINOUT.md` and the HAT's J4 breakout before soldering.
//!     rp2350b GP20 (MOSI)  ← rpi5 GPIO10 (SPI0 MOSI, pin 19)
//!     rp2350b GP21 (SCLK)  ← rpi5 GPIO11 (SPI0 SCLK, pin 23)
//!     rp2350b GP22 (CS)    ← rpi5 GPIO8  (SPI0 CE0,  pin 24)  ** REQUIRED **
//!     rp2350b GP26 (READY) → rpi5 GPIO25 (input,     pin 22)
//!     common GND.  CS frames each transfer; without it the SM parks forever at
//!     the CS-wait (rx fps stays 0). The rpi5 drives CE0 automatically for
//!     /dev/spidev0.0 — just wire it. Keep SCLK/MOSI short and, ideally, each
//!     twisted with its own GND return; a single far-away ground on a flying-wire
//!     bus rings and can corrupt data mid-frame (CS framing fixes sync, not SI).
//!
//! RP2350 input erratum E9 (§3.2): the SPI inputs are configured **pull-down**
//! so a floating bus (rpi5 disconnected/idle) can't latch the pad high. External
//! pulls on the HAT are preferable; the internal pull is the firmware backstop.
//!
//! ## Bring-up (no logic analyzer): use the `pico` as a sigrok LA on MOSI/SCLK/
//! READY at a low SPI clock first; confirm byte order and the READY/CE timing,
//! then ramp the clock. Until the rpi5 is wired, this binary builds and arms but
//! will simply wait at READY (no SCLK = no bytes), which is the correct idle.
//!
//! Run:
//!     cargo run --bin phase5-spi

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
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 5 SPI link"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// Full 256×64 wall (two chains of four 64×32 panels), 8-bit BCM. Must match the
// host packer's geometry (rayglow/render/hub75.py) and Phase 4.
const W: usize = 256;
const H: usize = 32;
const B: usize = 8;
// HUB75 pixel clock = sys_clk / (2*div). (2,0)=37.5MHz was clean over ONE chain
// in Phase 3, but the full two-chain wall on flying-wire jumpers (3.3V direct, no
// '245 buffers yet) shows down-chain SI on the R1G1B1 lines. (6,0)=12.5MHz to
// test/mitigate; raise back toward (2,0) once the HAT's level-shifters are in.
const DATA_CLK_DIV: (u16, u8) = (6, 0); // ~12.5 MHz pixel clock (SI-safe for now)
const OE_GAIN: u32 = 6; // brightness gain for the wide wall (see Phase 4).

// Framebuffer size in BYTES = one SPI frame. fb_cells is u16 count → ×2.
const FRAME_BYTES: u32 = (hub75::fb_cells(W, H, B) * 2) as u32; // 65536

// SPI-link GPIO. MOSI is the PIO IN base; SCLK and CS are sampled with
// `wait gpio` (absolute), so they are hardcoded in the PIO program below — keep
// these consts in sync with the literals there.
const MOSI_PIN: u8 = 20;
const SCLK_PIN: u8 = 21;
const CS_PIN: u8 = 22; // chip-select (CE0), active low — frame boundary
const READY_PIN: u8 = 26;
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

    // --- SPI-RX pin setup -------------------------------------------------
    // MOSI + SCLK into PIO1 with pull-down (E9 backstop). CS is active-low, so it
    // gets a pull-UP (idles high between frames). Bind them so the pads stay
    // configured for the program's lifetime.
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

    // --- SPI-RX PIO program (mode 0, CS-framed) ----------------------------
    // The frame boundary is the chip-select edge, NOT a bit count — this is what
    // makes reception immune to idle-line noise and handshake jitter. Per frame:
    //   wait CS high (idle) then CS low (fresh frame start), then sample MOSI on
    //   each SCLK rising edge, MSB-first, autopush at 8 bits → one byte per FIFO
    //   entry → byte-size DMA → in-order framebuffer bytes.
    // `restart()` (called each frame by the CPU) jumps to wrap_target = the CS
    // preamble and zeroes the shift counter, so every frame re-aligns to a fresh
    // CS edge with bit 0 = first byte. The CPU may restart while CS is still low
    // (tail of the previous frame); `wait 1 gpio 22` then catches CS's rising
    // edge first, so we always lock onto the NEXT frame's falling edge.
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
        "phase5-spi: {}x{} wall ready. CS-framed SPI-RX on PIO1 (MOSI GP{}, SCLK GP{}, CS GP{}), READY GP{}. frame = {} bytes.",
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
