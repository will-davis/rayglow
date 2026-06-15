//! Golden reference generator for the rp2350b SPI bit-plane contract.
//!
//! Emits three files in the working directory:
//!   - `gamma_lut.bin`   — 256 × u16 LE: the CIE/gamma LUT (γ=2.1, 8-bit) that
//!                         firmware/src/lut.rs builds. Same for R/G/B (Rgb888).
//!   - `golden_input.bin`— 64×256×3 bytes: the deterministic test frame, row-major
//!                         (y outer 0..63, x inner 0..255, then R,G,B). Linear RGB.
//!   - `golden_frame.bin`— 65536 bytes (32768 × u16 LE): the packed framebuffer
//!                         exactly as `Display::render` would leave the inactive
//!                         buffer for that input. THIS is the byte-match target.
//!
//! The math below is copied verbatim from the firmware so the host packer can be
//! proven byte-identical to it. W/H/B and γ mirror firmware/src/bin/phase4_anim.rs.

use std::fs::File;
use std::io::Write;

// Per-chain geometry (firmware consts). Wall = W × 2H = 256 × 64.
const W: usize = 256;
const H: usize = 32;
const B: usize = 8;
const GAMMA: f32 = 2.1;

// --- verbatim from firmware/src/lut.rs: calculate_lookup_value -------------
fn calculate_lookup_value(index: usize, source_max: u16, target_max: u16, gamma: f32) -> u16 {
    let max = target_max as f32;
    let remapped = index as f32 / source_max as f32 * max;
    let value = libm::roundf(max * libm::powf(remapped / max, gamma));
    u16::try_from(value as u32).unwrap_or(0)
}

// Rgb888: MAX_R = MAX_G = MAX_B = 255; B-bit target_max = (1<<B)-1.
fn build_lut() -> [u16; 1 << B] {
    let mut lut = [0u16; 1 << B];
    let target_max = ((1usize << B) - 1) as u16;
    for (i, slot) in lut.iter_mut().enumerate() {
        *slot = calculate_lookup_value(i, 255, target_max, GAMMA);
    }
    lut
}

/// Deterministic test pattern. Chosen to exercise every bit-plane, both chains
/// (y<32 / y>=32), both scan-halves, the column inversion, and full gamma range
/// with three distinct per-channel value sequences.
fn pixel(x: usize, y: usize) -> (u8, u8, u8) {
    let r = (x & 0xff) as u8;
    let g = ((y * 4) & 0xff) as u8;
    let b = ((x ^ (y * 3)) & 0xff) as u8;
    (r, g, b)
}

fn main() {
    let lut = build_lut();

    // --- verbatim packing from firmware/src/lib.rs: Display::render ---------
    // (brightness == 255, so the per-pixel brightness scaling branch is skipped,
    //  exactly as the firmware does at full brightness.)
    let mut fb = vec![0u16; W * H / 2 * B]; // fb_cells(W,H,B) = 32768

    for y in 0..(2 * H) {
        let chain = y / H;
        let yc = H - 1 - (y % H); // panel-mount inversion
        let half = yc > (H / 2) - 1;
        let shift = (chain * 6) + if half { 3 } else { 0 };
        let mask = !(0b111u16 << shift);
        let row_base = (yc % (H / 2)) * W * B;

        for x in 0..W {
            let (r, g, bch) = pixel(x, y);
            let c_r = lut[r as usize];
            let c_g = lut[g as usize];
            let c_b = lut[bch as usize];
            let base = (W - 1 - x) + row_base;
            for bp in 0..B {
                let packed = (((c_b >> bp) & 1) << 2
                    | ((c_g >> bp) & 1) << 1
                    | ((c_r >> bp) & 1))
                    << shift;
                let idx = base + bp * W;
                fb[idx] = (fb[idx] & mask) | packed;
            }
        }
    }

    // --- dump LUT (u16 LE) -------------------------------------------------
    let mut f = File::create("gamma_lut.bin").unwrap();
    for v in lut.iter() {
        f.write_all(&v.to_le_bytes()).unwrap();
    }

    // --- dump input frame (row-major RGB bytes) ----------------------------
    let mut f = File::create("golden_input.bin").unwrap();
    for y in 0..(2 * H) {
        for x in 0..W {
            let (r, g, b) = pixel(x, y);
            f.write_all(&[r, g, b]).unwrap();
        }
    }

    // --- dump packed framebuffer (u16 LE) ----------------------------------
    let mut f = File::create("golden_frame.bin").unwrap();
    for v in fb.iter() {
        f.write_all(&v.to_le_bytes()).unwrap();
    }

    eprintln!(
        "wrote gamma_lut.bin ({} u16), golden_input.bin ({} bytes), golden_frame.bin ({} bytes)",
        lut.len(),
        2 * H * W * 3,
        fb.len() * 2
    );
}
