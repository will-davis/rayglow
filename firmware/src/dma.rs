//! Splits the DMA block into individually-owned channels and exposes their raw
//! register block.
//!
//! Ported from kjagiello's `hub75-pio-rs` (`src/dma.rs`), which in turn lifted
//! it from rp-hal PR #209. The HUB75 engine needs low-level control the safe
//! HAL DMA API doesn't expose (`chain_to`, `treq_sel`, and writing the aliased
//! `ch_al2_write_addr_trig` register to self-retrigger the loop channels), so
//! we drive the channel registers directly.
//!
//! **RP2040 → RP2350 port:** the only change from the original is how a uniform
//! per-channel register block is obtained. rp2040-pac exposes `DMA.ch[i]` (an
//! array); rp235x-pac (newer svd2rust) exposes `DMA.ch(i)` (a method). The
//! `pac::dma::CH` block type and its register names are otherwise identical —
//! note that rp235x-pac register accessors are *methods* (`ch_al1_ctrl()`),
//! whereas rp2040-pac used *fields* (`ch_al1_ctrl`). That parenthesisation
//! happens in `lib.rs` where the registers are written.

use core::marker::PhantomData;
use rp235x_hal::pac;
use rp235x_hal::pac::DMA;

/// DMA unit.
pub trait DMAExt {
    /// Splits the DMA unit into its individual channels.
    fn split(self) -> Channels;
}

/// DMA channel.
pub struct Channel<CH: ChannelIndex> {
    _phantom: PhantomData<CH>,
}

/// DMA channel identifier.
pub trait ChannelIndex {
    /// Numerical index of the DMA channel (0..=11 — the RP2350 has 16 channels,
    /// but this engine only needs four and mirrors the original's 12-channel
    /// table).
    fn id() -> u8;
}

macro_rules! channels {
    (
        $($CHX:ident: ($chX:ident, $x:expr),)+
    ) => {
        impl DMAExt for DMA {
            fn split(self) -> Channels {
                Channels {
                    $(
                        $chX: Channel {
                            _phantom: PhantomData,
                        },
                    )+
                }
            }
        }

        /// Set of DMA channels.
        pub struct Channels {
            $(
                /// DMA channel.
                pub $chX: Channel<$CHX>,
            )+
        }
        $(
            /// DMA channel identifier.
            pub struct $CHX;
            impl ChannelIndex for $CHX {
                fn id() -> u8 {
                    $x
                }
            }
        )+
    }
}

channels! {
    CH0: (ch0, 0),
    CH1: (ch1, 1),
    CH2: (ch2, 2),
    CH3: (ch3, 3),
    CH4: (ch4, 4),
    CH5: (ch5, 5),
    CH6: (ch6, 6),
    CH7: (ch7, 7),
    CH8: (ch8, 8),
    CH9: (ch9, 9),
    CH10:(ch10, 10),
    CH11:(ch11, 11),
}

pub trait ChannelRegs {
    unsafe fn ptr() -> *const pac::dma::CH;
    fn regs(&self) -> &pac::dma::CH;
}

impl<CH: ChannelIndex> ChannelRegs for Channel<CH> {
    unsafe fn ptr() -> *const pac::dma::CH {
        // rp235x-pac: `ch(i)` accessor (vs rp2040-pac's `ch[i]` array index).
        (*pac::DMA::ptr()).ch(CH::id() as usize)
    }

    fn regs(&self) -> &pac::dma::CH {
        unsafe { &*Self::ptr() }
    }
}
