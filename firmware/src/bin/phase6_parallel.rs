//! Phase 6 — Pi 5 → RP2350 **4-lane parallel** link (Workstream 3).
//!
//! A widened sibling of `phase_experimental`: identical single-chain scan-out
//! engine, identical CS-framing + READY handshake, identical RX DMA + framebuffer
//! drop — the ONLY change is the ingest data path. Phase 5 / experimental clock
//! one MOSI bit per SCLK edge (8 edges/byte); this clocks **4 data lanes per edge
//! = a nibble per edge, 2 edges/byte**, fed by the Pi 5's RP1 PIO block.
//!
//! ## Why 4 lanes (not 8)
//! The board exposes GP0–27 only. The scan-out engine owns GP0–18, leaving GP19–27
//! (9 pins) for the link; 8 data lanes + DCLK + CS + READY = 11 won't fit. 4 lanes
//! (4 data + 3 control = 7) fits with room, divides a byte cleanly (2 nibbles), and
//! still lifts the link off the critical path: at the Pi's clkdiv 2 that's
//! 4×50 MHz = 200 Mbit/s → a 32 KB frame in ~1.3 ms (vs ~6.6 ms over 40 MHz SPI).
//!
//! ## Byte order (why the stream stays byte-identical)
//! `in pins, 4` samples lanes DATA0..3 = one nibble; autopush at 8 = two samples
//! per byte. RX shifts **left** (like the proven single-lane path) so the byte
//! lands in ISR[7:0] where the byte-size DMA reads it — first nibble sampled →
//! HIGH nibble. So the Pi must send the HIGH nibble of each byte first: it does a
//! cheap per-byte nibble-swap then `out pins, 4` shift-right (autopull 32, byte
//! order preserved). Net: the framebuffer ends byte-identical to
//! `hub75.py::pack_single`. **Validate on a logic analyzer first** with a
//! 0x00,0x01,0x02… ramp: if every byte's nibbles are swapped, toggle the Pi's
//! `nibble_swap` (PioOut); if bits within a nibble mirror, reverse the lane
//! wiring. (This ordering can't be proven from the desk.)
//!
//! ## Pin map  (RP2350b GP ↔ rpi5 BCM ↔ signal)
//!     DATA0..3  GP20..GP23   ← rpi5 GPIO12..GPIO15   (4 lanes, IN base = GP20)
//!     DCLK      GP24         ← rpi5 GPIO20           (Pi-driven data clock)
//!     CS        GP25         ← rpi5 GPIO21           (active-low frame boundary)
//!     READY     GP26         → rpi5 GPIO25 (input)   (RP2350 → Pi: armed)
//!     common GND (a return beside the lane bundle; keep short — lanes at speed on
//!     flying wire ring badly). GP19 + GP27 are spare.
//! Scan-out engine pins are unchanged (GP0–18). DATA0..3 must stay CONTIGUOUS
//! (the `in pins, 4` group); DCLK/CS are read by absolute `wait gpio` so they are
//! hardcoded in the PIO program below — keep the consts in sync with the literals.
//!
//! ## Two-chain swap (when the custom HAT lands)
//! This binary is single-chain (`hub75::single`, u8 cells) to match the current
//! rig. The parallel RX here is chain-agnostic; to drive the two-chain wall, change
//! only the three marked SINGLE-CHAIN lines (Display1→Display, DisplayMemory1→
//! DisplayMemory, FRAME_BYTES ×2) exactly as `phase5_spi` differs from
//! `phase_experimental`, and pack with `pack()` instead of `pack_single()`.
//!
//! Run:
//!     cargo run --bin phase6-parallel

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
    hal::binary_info::rp_program_description!(c"RP2350 RGB driver - Phase 6 parallel 4-lane link"),
];

const XTAL_FREQ_HZ: u32 = 12_000_000;

// A/B KNOB — panels daisy-chained on the single chain. W = 64 * this.
//   8 = full wall   (512 wide, 64 KB frame)   (u8 cells: fb_cells(512,32,8))
//   4 = one panel row (256 wide, 32 KB frame)  ← for fps/SI A/B testing
// MUST match the Pi's `len(SPI_CHAIN_ORDER)` (config.py): both sides derive the
// frame byte-count from it, and the handshake is a FIXED-size contract (the RX
// DMA waits for exactly FRAME_BYTES) — a mismatch desyncs the link. Reflash to
// change (W is a compile-time const generic). Chain A only (GP0–5); B idle/black.
const PANELS_IN_CHAIN: usize = 4;
const W: usize = 64 * PANELS_IN_CHAIN;
const H: usize = 32;
const B: usize = 8;
// HUB75 pixel clock = sys_clk / (2*div), sys_clk = 150 MHz. Unchanged from the
// single-chain bring-up; tune independently of the (faster) ingest clock.
const DATA_CLK_DIV: (u16, u8) = (3, 0); // ~25 MHz pixel clock (150/(2*3))
// Brightness gain (Phase 4 §set_oe_gain) — carried from phase_experimental.
const OE_GAIN: u32 = 64;

// Framebuffer size in BYTES = one parallel frame. SINGLE-CHAIN u8 cells (no idle
// chain-B half), so NO ×2: fb_cells(256,32,8) = 32768 = 32 KB at 4 panels,
// fb_cells(512,32,8) = 65536 = 64 KB at 8. Must match rayglow/render/hub75.py::
// pack_single. (Two-chain swap: ×2, and use pack().)
const FRAME_BYTES: u32 = hub75::fb_cells(W, H, B) as u32;

// Frame-RX stall timeout (see phase_experimental). A started-then-stalled transfer
// is corrupt — abort + drop instead of wedging; a not-yet-started transfer never
// trips it. Runs only during the idle ingest wait → no steady-state cost.
const RX_STALL_US: u32 = 50_000; // 50 ms with zero byte progress = dead transfer

// Parallel-link GPIO. DATA0 is the PIO IN base; the 4 lanes DATA0..3 must be
// CONTIGUOUS (GP20..GP23). DCLK and CS are sampled with absolute `wait gpio`, so
// they are hardcoded in the PIO program below — keep these consts in sync.
const DATA0_PIN: u8 = 20; // 4 lanes: GP20..GP23
const NUM_LANES: u8 = 4;
const DCLK_PIN: u8 = 24; // Pi-driven data clock
const CS_PIN: u8 = 25; // chip-select, active low — frame boundary
const READY_PIN: u8 = 26; // RP2350 -> Pi, armed-and-waiting (same pin as SPI READY)
const _: () = assert!(DATA0_PIN == 20, "PIO `in pins, 4` base must match DATA0_PIN");
const _: () = assert!(DCLK_PIN == 24, "PIO `wait gpio 24` must match DCLK_PIN");
const _: () = assert!(CS_PIN == 25, "PIO `wait gpio 25` must match CS_PIN");

static mut DISPLAY_BUFFER: hub75::single::DisplayMemory1<W, H, B> =
    hub75::single::DisplayMemory1::new(); // SINGLE-CHAIN (two-chain: DisplayMemory)

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
    // PIO1 → parallel receiver.
    let (mut pio1, rx_sm, _, _, _) = pac.PIO1.split(&mut pac.RESETS);

    // DMA: ch0–3 for the engine, ch4 for parallel-RX → framebuffer.
    pac.RESETS.reset().modify(|_, w| w.dma().set_bit());
    pac.RESETS.reset().modify(|_, w| w.dma().clear_bit());
    while pac.RESETS.reset_done().read().dma().bit_is_clear() {}
    let dma = pac.DMA.split();

    let lut = {
        let lut: GammaLut<B, Rgb888, _> = GammaLut::new();
        lut.init((2.1, 2.1, 2.1))
    };

    let mut display = unsafe {
        hub75::single::Display1::new( // SINGLE-CHAIN (two-chain: hub75::Display)
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

    // --- Parallel-RX pin setup --------------------------------------------
    // 4 data lanes (GP20..23) + DCLK into PIO1 with pull-down (E9 backstop, idle
    // low). CS is active-low, so it gets a pull-UP (idles high between frames).
    // Bind them so the pads stay routed to PIO1 for the program's lifetime.
    let _data: [_; 4] = [
        pins.gpio20.into_function::<FunctionPio1>().into_pull_type::<PullDown>().into_dyn_pin(),
        pins.gpio21.into_function::<FunctionPio1>().into_pull_type::<PullDown>().into_dyn_pin(),
        pins.gpio22.into_function::<FunctionPio1>().into_pull_type::<PullDown>().into_dyn_pin(),
        pins.gpio23.into_function::<FunctionPio1>().into_pull_type::<PullDown>().into_dyn_pin(),
    ];
    let _dclk = pins
        .gpio24
        .into_function::<FunctionPio1>()
        .into_pull_type::<PullDown>();
    let _cs = pins
        .gpio25
        .into_function::<FunctionPio1>()
        .into_pull_type::<PullUp>();
    // READY is a plain push-pull output (SIO), idle low until a frame is armed.
    let mut ready = pins.gpio26.into_push_pull_output();
    let _ = ready.set_low();

    // --- Parallel-RX PIO program (CS-framed, 4 lanes/clock) ----------------
    // Frame boundary = the CS edge, NOT a byte count (immune to idle-line noise +
    // handshake jitter). Per frame: wait CS high (idle) then CS low (fresh start),
    // then on each DCLK rising edge sample all 4 lanes (a nibble) → autopush at
    // 8 bits = one byte per TWO clocks → byte-size DMA → in-order framebuffer
    // bytes. `restart()` (each frame, CPU) re-parks at the CS preamble with shift
    // counter 0, so a dropped frame can't desync nibble/byte alignment.
    let program = pio::pio_asm!(
        ".wrap_target",
        "wait 1 gpio 25", // CS high  — idle / previous frame ended
        "wait 0 gpio 25", // CS low   — fresh frame start, shift counter = 0
        "nibloop:",
        "wait 1 gpio 24", // DCLK rising = sample point
        "in pins, 4",     // sample DATA0..3 (IN base = GP20) = one nibble
        "wait 0 gpio 24", // DCLK falling
        "jmp nibloop",    // next nibble (restart() re-parks at the CS preamble)
        ".wrap",
    );
    let installed = pio1.install(&program.program).unwrap();
    let (mut rx_sm, rx_fifo, _tx) = PIOBuilder::from_installed_program(installed)
        .in_pin_base(DATA0_PIN)
        // ShiftLeft so the assembled byte lands in ISR[7:0] for the byte DMA (the
        // proven single-lane placement); first nibble sampled becomes the HIGH
        // nibble, so the Pi sends high-nibble-first (PioOut nibble_swap).
        .in_shift_direction(ShiftDirection::Left)
        .autopush(true)
        .push_threshold(8) // one byte per TWO nibble samples
        .buffers(Buffers::OnlyRx)
        .clock_divisor_fixed_point(1, 0) // full system clock
        .build(rx_sm);
    // All sampled pins are inputs: the 4 data lanes + DCLK + CS.
    rx_sm.set_pindirs([
        (DATA0_PIN, PinDir::Input),
        (DATA0_PIN + 1, PinDir::Input),
        (DATA0_PIN + 2, PinDir::Input),
        (DATA0_PIN + 3, PinDir::Input),
        (DCLK_PIN, PinDir::Input),
        (CS_PIN, PinDir::Input),
    ]);

    let rx_ch = dma.ch4;
    let fifo_addr = rx_fifo.fifo_address() as u32;
    let rx_dreq = rx_fifo.dreq_value();

    info!(
        "phase6-parallel: {}x{} SINGLE-CHAIN wall (u8 cells, chain A). 4-lane CS-framed RX on PIO1 (DATA0 GP{}..GP{}, DCLK GP{}, CS GP{}), READY GP{}. frame = {} bytes.",
        W,
        H,
        DATA0_PIN,
        DATA0_PIN + NUM_LANES - 1,
        DCLK_PIN,
        CS_PIN,
        READY_PIN,
        FRAME_BYTES
    );

    let mut frames: u32 = 0;
    let mut drops: u32 = 0;
    let mut last_us: u32 = timer.get_counter_low();
    // Bring-up aid: address of the most recently received frame, so the per-second
    // telemetry can dump its first bytes for the nibble/lane-order check against a
    // known ramp from `tools/pio_ramp.py`. Validated 2026-06 (nibble_swap=True,
    // lanes correct, clean ≤ clkdiv 3); flip true to re-check after a rewire.
    const RX_DEBUG_BYTES: bool = false;
    let mut last_good: u32 = 0;
    let mut sm = rx_sm.start();

    // Stolen handle to the DMA block's global CHAN_ABORT register, used only to
    // halt a stalled RX channel for recovery. It's a different register from the
    // per-channel ones the engine drives, so this aliasing is benign.
    let dma_regs = unsafe { hal::pac::Peripherals::steal().DMA };

    loop {
        // Destination = the buffer not currently on screen. Recompute every
        // frame (flip swaps the roles).
        let dst = display.inactive_fb_ptr() as u32;

        // Fresh alignment: drain any stale RX byte and restart the SM so its
        // shift counter is 0 → the next DCLK edge is the first nibble of this
        // frame. Then arm the DMA to drain the FIFO into `dst`.
        sm.clear_fifos();
        sm.restart();
        arm_rx_dma(&rx_ch, fifo_addr, dst, FRAME_BYTES, rx_dreq);

        // Tell the rpi5 we're ready to receive this frame.
        let _ = ready.set_high();

        // Zero-CPU ingest: spin until the DMA has placed all FRAME_BYTES, with a
        // stall watchdog (see RX_STALL_US). Any byte-count progress resets the
        // timer; a transfer that STARTED then sat unchanged past the timeout is a
        // corrupt/short frame, so we abort the channel and drop it.
        let mut last_remaining = FRAME_BYTES;
        let mut progress_us = timer.get_counter_low();
        let mut dropped = false;
        while rx_busy(&rx_ch) {
            let remaining = rx_ch.regs().ch_trans_count().read().bits();
            if remaining != last_remaining {
                last_remaining = remaining;
                progress_us = timer.get_counter_low();
            } else if remaining < FRAME_BYTES
                && timer.get_counter_low().wrapping_sub(progress_us) > RX_STALL_US
            {
                abort_rx_dma(&dma_regs);
                dropped = true;
                break;
            }
        }

        let _ = ready.set_low();

        if dropped {
            // Partial buffer — keep the last good frame on screen; the SM restart
            // + re-arm at the top of the next loop resync on the next CS edge.
            drops = drops.wrapping_add(1);
        } else {
            // Show the freshly-received frame. `flip` (not `commit`): the RX DMA
            // overwrote the whole inactive buffer, so there is nothing to clear, and
            // flip avoids commit()'s racy `fb_loop_busy` wait that deadlocks under
            // this tight streaming cadence (see Display::flip).
            display.flip();
            last_good = dst; // buffer just filled (now on screen) — safe to read
        }

        frames += 1;
        let now = timer.get_counter_low();
        if now.wrapping_sub(last_us) >= 1_000_000 {
            info!("rx fps {} (drops {})", frames, drops);
            if RX_DEBUG_BYTES && last_good != 0 {
                // First 8 received bytes. Against a 0,1,2,3,… ramp: correct =
                // 00 01 02 03 04 05 06 07; nibble-swapped = 00 10 20 30 40 50 60 70.
                let p = last_good as *const u8;
                let b = unsafe {
                    [
                        p.read_volatile(),
                        p.add(1).read_volatile(),
                        p.add(2).read_volatile(),
                        p.add(3).read_volatile(),
                        p.add(4).read_volatile(),
                        p.add(5).read_volatile(),
                        p.add(6).read_volatile(),
                        p.add(7).read_volatile(),
                    ]
                };
                info!(
                    "  rx[0..8] = {:#04x} {:#04x} {:#04x} {:#04x} {:#04x} {:#04x} {:#04x} {:#04x}",
                    b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]
                );
            }
            frames = 0;
            drops = 0;
            last_us = now;
        }
    }
}

/// Aborts the parallel-RX DMA channel (CH4) via the DMA block's global CHAN_ABORT
/// register and waits for the abort to complete, leaving the channel idle and
/// safe to re-arm. Used by the frame-RX stall watchdog to recover from a corrupt
/// or short frame without a reflash.
fn abort_rx_dma(dma: &hal::pac::DMA) {
    dma.chan_abort().write(|w| unsafe { w.bits(1 << 4) }); // CH4
    while dma.chan_abort().read().bits() != 0 {}
}

/// (Re)arms the parallel-RX DMA channel for one frame: FIFO → framebuffer,
/// byte-size, write-incrementing, paced by the PIO RX DREQ. Writing the trigger
/// alias for the write address starts the channel.
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
