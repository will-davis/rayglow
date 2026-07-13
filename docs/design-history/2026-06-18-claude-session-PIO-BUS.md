 ▐▛███▜▌   Claude Code v2.1.179
▝▜█████▛▘  Opus 4.8 · Claude Max
  ▘▘ ▝▝    ~/Projects/rayglow/firmware


❯ Good evening! I would like to have a better fundamental of the RGB LED        
  matrices and how exactly the work and are being controlled by what we've      
  built here. Here is my mental model up until the point I lose confidence in   
  how parts function. Let me know what lines up and what is isn't right, and    
  help me through the logic of the rest so I have a comprehensive intuition:    
                                                                                
  The rp2350b PiZero ingests the the datastream through 1 PIO block dedicated   
  to SPI and pushes the information to the two other PIO blocks, each           
  responsible for 32 rows of pixels (2x 256x32). The rp2350b then pushes the    
  data to the two chains of 4x panels via HUB75 (R1, G1, B1, R2 [...] LAT, CLK, 
  OE). The specific RGB, CLK, and time rapid swithing time sensitive signals    
  are reserved for the PIO blocks specifically.                                 
                                                                                
  Per panel understanding:                                                      
  Each panel has 26 JXI5020 ICs that hold 16 bits of data, two of which are set 
  aside for multiplexing. The remaining 24 are are split into the top 16 and    
  bottom 16 rows of the 32 LEDs height. Four JXI5020 in series hold the entire  
  64 bit "word" for a single row of the panel for a single color. The 24        
  JXI5020 are divided into R1 G1 B1 accounting for the top 16 rows and R2 G2 B2 
  for the bottom 16 rows. The data is inserted in parallel for the all six      
  channels.                                                                     
                                                                                
  4 JXI5020 per row per color                                                   
  8 bytes per color totalling 64 pixels                                         
  3 colors                                                                      
  2 rows shows at the same time (1 of the top 16 R1G1B1 + 1 of the bottom 16    
  R2G2B2)                                                                       
                                                                                
  Once the bits are inserted for the entire row, the row is pulled to ground    
  via an ICN2012, allowing the current to flow through the LEDs, lighting up    
  the values on a single row. The two JXI5020 multiplexes through the 16 rows,  
  one for the top 16 one for the bottom 16.                                     
                                                                                
  This is where I start to get lost. The entire process loops, the ICN2012,     
  which take in a three bit address. 3 bits = 8 combinations of toggleable      
  outputs, which I assume is related to the ABCD channels. The clock is the     
  clock. LAT tells the shift registers when to actually publish the bit streams 
  (it latches the values so they can be sent). OE simply allows the LEDs a      
  path to ground or blocks it. How do the bit-planes work, which I'm assuming   
  is tied to the ICN2012s? What is the relation between OE and the ICN2012?     
  What is the complete picture?                                                 
                                                                                
  I've added the datasheets for those components here:                          
  `/home/will/Projects/rayglow/.reference/P6-3528-64X32-16S-HL11/`              
                                                                                
  Ultimately I'm trying to figure out, bit per bit, what is in each of the      
  packages sent over SPI and what is sent out to the displays, but I'm starting 
  with the physical displays.                                                   

● Good evening, Will. This is a great question to dig into — the gap between "I
  understand shift registers" and "I understand why the bit-planes and OE dance
  the way they do" is exactly where the interesting mechanics live. Let me
  ground my answer in your actual datasheets and firmware rather than generic
  HUB75 lore, since the details matter here.

  Let me pull up the relevant material first.

  Read 2 files, listed 1 directory (ctrl+o to expand)

● Let me read the two component datasheets so I'm describing your specific
  silicon, not generic parts.

  Read 2 files (ctrl+o to expand)

● I've got what I need from the datasheets and firmware. Let me confirm one
  thing about the SPI ingest path so I describe it accurately rather than from
  your assumption.

  Ran 1 shell command (ctrl+o to expand)

● Excellent question, and you've actually built a remarkably good intuition —
  most of your per-panel model is correct. The places you "lose confidence" are
  exactly the places where two different mechanisms get conflated. Let me grade
  your model, then rebuild the picture from the silicon up to the SPI byte.

  The single most important correction up front: bit-planes have nothing to do 
  with the ICN2012. Bit-planes are a time-domain brightness trick run entirely
  by the RP2350 through the OE pin. The ICN2012 just picks which row is
  electrically alive. They're orthogonal axes that happen to be choreographed
  together. Once that clicks, the rest falls into place.

  ---
  1. Scorecard on your mental model

  Your statement: RP2350 ingests SPI on one PIO block
  Verdict: ✅ Right — but it's a custom PIO SPI-slave (PIO1), not the hardware
    SPI peripheral. See §6.
  ────────────────────────────────────────
  Your statement: Two other PIO blocks, each driving 32 rows
  Verdict: ❌ The biggest misconception. It's 3 state machines in one PIO block 
    (PIO0), split by function (data / row / OE), not by rows. Both chains and
  all
     rows go through the same three SMs.
  ────────────────────────────────────────
  Your statement: HUB75 signals (RGB, CLK, LAT, OE, ADDR) are PIO-driven,
    timing-critical
  Verdict: ✅ Exactly right — CPU never touches them.
  ────────────────────────────────────────
  Your statement: 4 JXI5020 in series = 64 channels = one color of one half
  Verdict: ✅ Correct.
  ────────────────────────────────────────
  Your statement: 24 used for R1G1B1 (top) + R2G2B2 (bottom), data inserted in
    parallel across 6 channels
  Verdict: ✅ Correct.
  ────────────────────────────────────────
  Your statement: "26 JXI5020, 2 set aside for multiplexing"
  Verdict: ❌ It's 24 JXI5020 + 2 ICN2012. The two mux chips are a different 
  part
     number. The JXI5020 has zero multiplexing function — all 16 of its channels

    are column drivers.
  ────────────────────────────────────────
  Your statement: Row pulled to ground via ICN2012
  Verdict: ❌ Backwards — the ICN2012 is a high-side switch; it sources current
    from V+ into the row. Ground-side is the JXI5020. (§2)
  ────────────────────────────────────────
  Your statement: 3-bit address → 8 combos, related to ABCD
  Verdict: ⚠️ Half-right. Each ICN2012 decodes 3 bits → 8 rows; two of them + a 
    4th select bit = 16 rows (ABCD).
  ────────────────────────────────────────
  Your statement: LAT latches shift-register → outputs
  Verdict: ✅ Right.
  ────────────────────────────────────────
  Your statement: OE gates the LEDs' path to ground
  Verdict: ✅ Right (it enables/blanks the JXI5020 current sinks).

  So your panel almost certainly has 24 JXI5020 (column current-sinks) + 2 
  ICN2012 (16-way row mux) = 26 ICs — and you merged them into one part in your
  head.

  ---
  2. The two chips and the current path (the physical foundation)

  These are complementary halves of a current loop. An LED only lights when it
  has both a source above it and a sink below it.

  JXI5020 — "16-Channel Constant Current LED Sink Driver" (the COLUMN driver, 
  low side)

  From the block diagram (datasheet p.2): it's a classic shift register → output
  latch → output driver chain:
  - SDI → 16-bit shift register → SDO (cascadable; SDO of one feeds SDI of the
  next — that's your "4 in series").
  - CLK shifts SDI in on the rising edge.
  - LE (latch enable): transparent while high, latches on the falling edge —
  copies all 16 shift-register bits into the output latch. This is your HUB75
  LAT.
  - OE (active-low): when low, the latched bits enable their channels; when
  high, all 16 outputs are forced off regardless of latch contents.
  - The 16 outputs (OUT0..OUT15) are not voltage drivers — they're regulated 
  constant-current sinks (3–45 mA, set by one external R-EXT resistor). Each
  one, when enabled, pulls its column down to GND through a fixed current. That
  constant-current behavior is why LED brightness is uniform regardless of how
  many are lit or supply sag — it's the whole point of the part vs. a plain
  74HC595.

  ICN2012 — "8-Channel Power Switch for LED Display" (the ROW mux, high side)

  From its datasheet (p.2): it integrates a 74HC138 (3-to-8 decoder) + 8 
  P-channel MOSFETs. So a single 16-pin chip replaces "1× 74HC138 + 4×
  dual-MOSFET packages." It:
  - Takes a 3-bit address A0/A1/A2 + two enables E1/E2.
  - Decodes that to turn exactly one of OUT0..OUT7 P-MOSFETs on.
  - Each P-MOSFET (Rds(on) ~100 mΩ, 2.5 A) sources current from V+ (the ~5 V LED
  rail) into one row's common anode.

  The loop, for one lit pixel:

  +5V LED rail
      │
      ▼  ICN2012 P-MOSFET (high side) — selects the ROW (anode common)
   row anode ──►─┤ LED ├──►── column cathode
                                │
                                ▼  JXI5020 OUTn (low side) — selects the COLUMN,
                                   sinks a constant current to GND
                              GND

  So row select = high side = ICN2012, column data = low side = JXI5020, and the
  LED sits between them. Your panel is common-anode by row. Current flows down
  a column only if (a) that row's ICN2012 P-MOSFET is on, (b) that column's
  JXI5020 latch bit is 1, and (c) OE is low. All three gates must be open.

  ---
  3. The scan: 1:16 multiplexing and the two halves

  "16S" in P6-3528-64X32-16S = 1:16 scan: at any instant only 1 of 16
  row-addresses is lit, and the panel cycles through all 16 fast enough that
  persistence-of-vision fills in the rest.

  But the panel is 32 rows tall, not 16. The trick: one address drives two 
  physical rows at once — the "top half" and "bottom half":
  - R1/G1/B1 carry data for the top half (rows 0–15).
  - R2/G2/B2 carry data for the bottom half (rows 16–31).
  - Address N simultaneously lights physical row N (top, fed by R1G1B1) and row
  N+16 (bottom, fed by R2G2B2).

  16 addresses × 2 simultaneous rows = 32 rows. That's why HUB75 has six RGB
  lines, not three, and why you correctly counted 24 column drivers: 6 data
  lines × 4 chained JXI5020 each.

  In your firmware this is the H/2 you see everywhere: H = 32 per chain, H/2 = 
  16 address-rows. And your two parallel chains stack vertically — chain A is
  the top 64 wall-rows, chain B the bottom 64 — for a 256×128… no, wait: each
  chain is 256×64 (4 panels × 64×32 with the two-halves making 64 tall), and two
  chains stacked = 256×128? Let me be precise to your config: W=256, H=32
  per-chain, wall = W × 2H = 256×64. Chain A = top 32 rows, chain B = bottom 32.
  Both chains share CLK + ADDR and clock simultaneously — that's the "parallel
  chains cost nothing in refresh time" lever in lib.rs.

  ---
  4. Bit-planes / BCM — the concept you were missing

  Here's the thing the ICN2012 is not responsible for.

  An LED has no analog brightness here. The JXI5020 sink is either on (constant
  current) or off. One bit per pixel per color. So how do you get 256 levels of
  red?

  Answer: Binary Coded Modulation (BCM) — a form of PWM where you decompose the
  8-bit brightness into its 8 binary digits and show each digit (a "bit-plane")
  for a binary-weighted slice of time.

  For one address-row you don't shift it out once — you shift it out 8 times,
  once per bit-plane:

  ┌───────────┬──────────────────────────────┬────────────────────┐
  │ Bit-plane │    Which pixels are "on"     │ OE on-time (ticks) │
  ├───────────┼──────────────────────────────┼────────────────────┤
  │ 0 (LSB)   │ pixels whose value bit 0 = 1 │ 2⁰−1 = 0… (≈1)     │
  ├───────────┼──────────────────────────────┼────────────────────┤
  │ 1         │ value bit 1 = 1              │ 2¹−1 = 1           │
  ├───────────┼──────────────────────────────┼────────────────────┤
  │ 2         │ value bit 2 = 1              │ 2²−1 = 3           │
  ├───────────┼──────────────────────────────┼────────────────────┤
  │ …         │ …                            │ …                  │
  ├───────────┼──────────────────────────────┼────────────────────┤
  │ 7 (MSB)   │ value bit 7 = 1              │ 2⁷−1 = 127         │
  └───────────┴──────────────────────────────┴────────────────────┘

  A red value of 0b1011_0101 (181) lights its red LED during planes 0, 2, 4, 5,
  7 → on for 1+4+16+32+128 = 181 of the 255 total ticks. Your eye integrates the
  flicker into "71% brightness." That's exactly the delays[] table:

  arr[i] = (1 << i) - 1;   // 0,1,3,7,15,31,63,127  → binary weights

  This is why the framebuffer is sized W * H/2 * B and why hub75.py does
  (channel >> plane) & 1 — it's slicing each 8-bit gamma-corrected value into 8
  separate on/off bitmaps. The bit-planes live entirely in the controller + the 
  OE timing. The JXI5020 only ever holds one plane's worth of on/off bits at a
  time; it has no idea brightness exists.

  (Aside on why BCM and not classic PWM: BCM needs only log₂(levels) shift-outs
  per row — 8 — instead of levels — 256. Massively less data to clock. The cost
  is you must hold the MSB plane ~128× longer than the LSB, which is the
  delays[] weighting.)

  ---
  5. OE ↔ ICN2012 relationship (two clocks, very different rates)

  ┌──────────┬────────────────────────┬─────────────────────────────────────┐
  │          │ ICN2012 (row address)  │ OE (the JXI5020 blanking/BCM gate)  │
  ├──────────┼────────────────────────┼─────────────────────────────────────┤
  │ Picks    │ Which row pair is      │ Whether + how long the lit row      │
  │          │ alive                  │ conducts                            │
  ├──────────┼────────────────────────┼─────────────────────────────────────┤
  │ Changes  │ Once per row period    │ Pulses once per bit-plane (8 per    │
  │          │ (16/frame)             │ row → 128/frame)                    │
  ├──────────┼────────────────────────┼─────────────────────────────────────┤
  │ Driven   │ row SM, ADDR pins      │ OE SM, binary-weighted from         │
  │ by       │                        │ delays[]                            │
  └──────────┴────────────────────────┴─────────────────────────────────────┘

  Why OE must exist independent of the row mux — two reasons:

  1. Anti-ghosting. When you change the ICN2012 address, or while you're
  shifting new bits into the JXI5020s, the wrong data is momentarily present on
  the wrong row. If the LEDs were live during that, you'd get smearing/ghosting
  up the column. So OE is forced high (blanked) during every shift and every
  address change, then pulsed low only when a stable, latched plane is sitting
  on the correct row.
  2. It's the brightness knob. The duration of the OE-low pulse is the bit-plane
  weight. Same data, longer OE-low = brighter. That's literally what
  set_oe_gain() exploits.

  So the sequence per address-row is: [shift plane 0 (OE high)] → [latch] → [OE 
  low for 0 ticks] → [shift plane 1 (OE high)] → [latch] → [OE low for 1 tick] →
  … → [plane 7, OE low 127 ticks] → advance ICN2012 address.

  ---
  6. The complete picture: how the RP2350 orchestrates it

  This is where your "two PIO blocks for 32 rows each" model needs replacing.
  The real division is by signal function, and it's gorgeous because the three
  state machines hand off to each other with IRQs and the CPU is never involved.

  PIO0 = the scan-out engine, 3 state machines (from lib.rs):

  - data SM — owns the 12 RGB pins (GP0–11, both chains) via out pins, and CLK
  via sideset. It clocks W pixels out of the framebuffer for the current
  (address-row, plane), toggling CLK each pixel. out pins, 16 per pixel (12
  mapped to GPIO, top 4 discarded — keeps clean 32-bit autopull = 2
  pixels/word). When the row is fully shifted: irq 4 ("data ready"), then wait 
  irq 5 ("go again").
  - row SM — owns the 4 ADDR pins (GP12–15) via out pins and LAT via sideset.
  Sets the ICN2012 address, then loops over B planes: waits irq 4, raises LAT +
  fires irq 6 (tells OE "display this"), drops LAT + fires irq 5 (tells data
  "shift next plane"), waits irq 7 (OE done), repeats; then advances the
  address.
  - OE SM — owns OE via sideset, and the BCM loop: pull a delay count from
  delays[], hold OE high until irq 6, then jmp x-- delay with OE low for that
  many ticks (the binary weight), then irq 7.

  The IRQ handshake (4/5/6/7) is the entire refresh loop — three tiny programs
  ping-ponging. 4 DMA channels feed them with zero CPU: two stream the
  framebuffer to the data SM in an endless self-resetting loop (fb_ch +
  fb_loop_ch), two stream delays[] to the OE SM the same way.

  PIO1 = the SPI receiver, 1 state machine (phase5_spi.rs):

  Your instinct here was right — one PIO block does SPI ingest. The nuance: it's
  not the chip's hardware SPI peripheral. They deliberately "trade a peripheral
  for a PIO block" (header comment) and hand-wrote a PIO SPI-slave: wait gpio 
  21 (SCLK) clocks MOSI (GP20) in, gated by CS (GP22). DMA ch4 drains its RX
  FIFO straight into the inactive framebuffer (inactive_fb_ptr()), byte by byte.
  When 65536 bytes have landed, the firmware flips buffers. So the ingest path
  is also zero-CPU-in-the-loop — it mirrors the scan-out philosophy.

  So the corrected one-liner: PIO0's three SMs scan both chains out together 
  (split by data/row/OE, not by rows); PIO1's one SM sucks the next frame in 
  over SPI; 5 DMA channels keep all of it fed without the CPU.

  ---
  7. Bit-for-bit: what's actually in the SPI stream

  This is your endgame question, and hub75.py is the exact spec. Per frame:

  - Total: 65536 bytes = 32768 × u16 cells, little-endian. That's W·H/2·B·2 = 
  256·16·8·2.
  - One cell = one (address-row, bit-plane, column) coordinate. Memory order
  (the order the data SM reads, looping forever):
  idx = addr_row*(W*B) + plane*W + (W-1-x)
        └ outer: 16 rows  └ middle: 8 planes  └ inner: 256 cols (X-mirrored for
  panel mount)
  - Inside each 16-bit cell, the bits map directly onto GP0–11 (the data SM's
  out pins, 16):

   bit: 15..12 │ 11 10  9 │  8  7  6 │  5  4  3 │  2  1  0
        unused │ B2 G2 R2 │ B1 G1 R1 │ B2 G2 R2 │ B1 G1 R1
               └ chain B (GP6–11) ────┘ └ chain A (GP0–5) ──┘
                  bottom    top           bottom    top
  - i.e. bits 0–2 = chain-A top-half pixel (R1G1B1), 3–5 = chain-A bottom
  (R2G2B2), 6–8 = chain-B top, 9–11 = chain-B bottom. Top 4 bits are padding the
  data SM throws away.
  - Each bit is one LED's on/off state for that one bit-plane. Not a brightness
  — a single binary "is this LED lit during plane p." The brightness
  reconstitutes in time via the OE weighting (§4).
  - Before the bit-slicing, the host applies the gamma LUT (config.SPI_GAMMA = 
  2.1, CIE curve, byte-identical to the firmware's lut.rs). Critical invariant:
  the renderer reads back LINEAR, the packer owns gamma — gamma is applied here,
  exactly once, then sliced into planes.

  So a full trace of one byte's journey: shader renders a linear-RGB pixel →
  gamma LUT maps 0–255 → 0–255 → for each of 8 planes, extract bit → OR into the
  right 3-bit field of the right u16 cell at idx → 65536 bytes ship over SPI
  (PIO1 + DMA ch4) → land in the inactive framebuffer untouched → buffer flips →
  data SM streams cell-by-cell to GP0–11, CLK shifting each into the JXI5020
  chains → row SM latches a plane and addresses the ICN2012 → OE SM holds the
  LEDs lit for that plane's binary weight → 8 planes × 16 addresses later, one
  full frame has been painted, and the eye integrates 128 OE pulses per row into
  a smooth 24-bit color image.

  That's the whole machine, end to end. The two leverage points worth
  internalizing: (a) brightness is time, not current (BCM via OE), and (b) the 
  controller never splits work by rows — it splits by signal (data/row/OE), 
  drives both chains in lockstep, and lets DMA + PIO IRQs run the refresh with 
  the CPU asleep.

  Want me to go the other direction next — trace the audio feature packet (the
  564-byte MILK struct) bit-for-bit, or walk the tools/verify.py golden-frame
  proof that shows the Python packer and Rust firmware are provably identical?

✻ Crunched for 3m 27s

※ recap: You wanted a fundamental understanding of how the RGB matrices work and how your RP2350 firmware drives them. I delivered a full bit-for-bit explanation correcting your mental model. Next, I can trace the audio feature packet or the verify.py golden-frame proof if you want. (disable recaps in /config)

❯ Firstly - thank you, that was lucid and I followed, gaining a much more       
  comfortable intuition for the sequence. I also gained more insight into the   
  the PIO blocks which are really cool little gizmos.                           
                                                                                
  I guess what I was ultimately trying to dissect was: The intent of this whole 
  project was to utilize the PIO blocks for their extremely fast (ASIC-like or  
  FPGA-like) PIO blocks. It sounds like we have functionally accomplished       
  that. What trips me up is that ALL of this data is going over a single SPI    
  interface and ultimately that has become the bottleneck. With four panels I   
  max out around 55 FPS, which is fine, it was what I was accomplishing with a  
  single Raspberry Pi 4b and the Adafruit hat, with 8 panels it maxes out       
  around 30 FPS. Fine for movies, video, not ideal for specifically an audio    
  visualizer showcasing fragment shaders. This is a bit out of scope for *this* 
  project, but do have any ideas on other changes, equipment, hardware, etc, I  
  could utilize to get that closer to 120 FPS? I would like it to be small      
  embedded electronics, but I do have spare graphics cards etc. I also wouldn't 
  mind tinkering with FPGA. Some of my thought is that I'm looking at this      
  PiZero and I see an HDMI port. I know the device also has a DVI interface.    
  Why is it so hard to interface with RGB matrix at a high framerate versus for 
  example, a 4k monitor? The rpi5 can handle 4k, which is 1000x the pixels of   
  the RGB matrix. It just feels like I'm missing some form hardware interface   
  that translates what the rpi5 can send over a *single* SPI data wire + clk    
  (for the most part, I realize there are other GPIOs being used as             
  controls/checks). A lot of this is the nature of the matrix itself, the       
  massive multiplier on clock frequency to get the shift registers serialed in  
  then holding them on for brightness, and maybe that's ultimately the          
  fundamental hurdle. Regardless, I'm curious your thoughts.                    

● Thank you — and you've actually zeroed in on exactly the right question, but I
  think you've misdiagnosed which stage is the wall. Let me check your actual
  SPI clock and frame sizes so I can give you numbers instead of hand-waving,
  because the math here is decisive and a little surprising.

  Searched for 1 pattern, ran 1 shell command (ctrl+o to expand)

● Found the smoking gun. Your default SPI clock is 24 MHz (__main__.py:254), and
  — tellingly — your own code already computes spi_floor_ms = last_bytes * 8 / 
  spi_hz (__main__.py:220). Past-you (with AI help) already instrumented the
  exact thing that's biting you. Let me make the diagnosis quantitative, because
  it completely reframes your question.

  The bottleneck is not the matrix. It's not even close.

  Let me compute both ceilings for your 8-panel wall (256×64, two-chain, 64
  KB/frame).

  Scan-out ceiling (RP2350 PIO → panels):
  - Pixel clock ≈ 37.5 MHz, so shifting one 256-wide plane = 256 / 37.5e6 ≈ 6.83
  µs.
  - A full frame = 16 address-rows × 8 bit-planes = 128 shift-outs → 128 × 6.83 
  µs ≈ 874 µs.
  - BCM lit-time adds ~26 µs (the delays[] sum, 247 ticks/row × 16 @ ~6.67 ns).
  - Total ≈ 0.9 ms/frame → ~1,100 FPS.

  Transport ceiling (Pi5 → RP2350 over SPI):
  - 24e6 / 8 / 65536 ≈ **45.8 FPS**.

  There's your answer in one line: the engine you built can scan at ~1,100 FPS; 
  the SPI pipe feeding it tops out at ~46. You have ~24× of headroom locked
  behind a drinking straw. And the signature is unmistakable — your FPS scales
  inversely with byte count (55 at 32 KB-ish, 30 at 64 KB), which is the
  fingerprint of a transport-bandwidth wall, not a render or scan-out wall. (The
  gap between the 46 ceiling and your real 30 is the Pi-side render+pack+commit
  overhead — secondary, but real; the numpy pack() still has a 64-iteration
  Python loop that's worth vectorizing away.)

  So your intuition that "the matrix clock multiplier is the fundamental hurdle"
  is exactly backwards. The matrix is the fast part. The PIO gamble paid off —
  handsomely. You just strangled it on ingest.

  Why HDMI is "easy" and SPI is "hard" — the real lesson

  You framed it as "I'm missing some hardware interface that translates what the
  Pi sends over one wire." Two distinct things are hiding in that sentence, and
  separating them dissolves the confusion:

  1. The translator (raster pixels → bit-planes + BCM timing + framebuffer).
  This is the genuinely hard, stateful part: a matrix is a dumb device — just
  shift registers and switches, no framebuffer, no serializer, no brightness
  logic. Something must hold a frame in RAM and re-emit it as binary-weighted
  planes. You already built that translator — it's your RP2350. You're not
  missing it.
  2. The transport (getting pixels into the translator fast). This is what's
  actually missing, and it's where SPI vs HDMI diverge:

  ┌───────────┬──────────────────────────┬─────────────────────────────────┐
  │           │ SPI (what you're using)  │  HDMI/DVI (what a 4K monitor    │
  │           │                          │              uses)              │
  ├───────────┼──────────────────────────┼─────────────────────────────────┤
  │ Lanes     │ 1 single-ended data wire │ 3 differential TMDS pairs +     │
  │           │                          │ clock                           │
  ├───────────┼──────────────────────────┼─────────────────────────────────┤
  │           │ General-purpose, ~tens   │ Purpose-built gigabit           │
  │ Signaling │ of MHz before SI dies on │ differential, ~3.4 Gbit/s per   │
  │           │  jumper wire             │ lane                            │
  ├───────────┼──────────────────────────┼─────────────────────────────────┤
  │ Who       │ A peripheral block       │ A dedicated display-controller  │
  │ drives it │ bit-shifting bytes       │ ASIC + TMDS serializer, DMA     │
  │           │                          │ straight from VRAM, zero CPU    │
  ├───────────┼──────────────────────────┼─────────────────────────────────┤
  │ Aggregate │ ~24 Mbit/s (you)         │ ~12 Gbit/s (4K60)               │
  └───────────┴──────────────────────────┴─────────────────────────────────┘

  That's a 500× transport gap, and it's why "4K is 1000× the pixels but easy":
  the GPU never spends a CPU cycle on it, and the wire was engineered from the
  ground up for gigabit video. SPI was designed to talk to flash chips and ADCs.
  You're asking a part designed for register pokes to carry a video stream. The
  pixel count was never the hard part — the fat, dedicated, zero-CPU transport
  is.

  Options ladder, cheapest → most ambitious

  0. Free: just turn the clock up. You're at 24 MHz with no apparent reason. The
  RP2350 PIO SPI-slave will happily clock faster; the wall is signal integrity
  on your wiring. Try --spi-hz 50000000. At 50 MHz: 50e6/8/65536 ≈ 95 FPS at 8
  panels — you'd likely exceed 120 at your 4-panel size today. Keep MOSI/SCLK
  short, add a ground return right alongside them, maybe a ~22 Ω series resistor
  at the Pi end to tame ringing. This is a 2-minute experiment that might end
  the whole quest. Push to 62 MHz (63 Mbit/s) and you hit 120 at 64 KB.

  1. Cheap + stays in your build: parallel SPI lanes. The RP2350 has 3 PIO
  blocks; you're using one SM of PIO1 for ingest. Split the wall into N vertical
  stripes, run N independent SPI-slave SMs, each on its own CE from the Pi (or
  a second SPI bus). 2 lanes at 31 MHz = 62 Mbit/s aggregate with easier SI per
  lane than one fast lane. This is the most "honors what you already built" path
  — your scan engine doesn't change at all, you just widen the funnel. Quad-SPI
  (4 data lines) is the same idea in one connector, but the Pi5's QSPI for
  arbitrary streaming is awkward, so discrete lanes are simpler.

  2. Fatter Pi transport: DPI parallel video. The Pi5 can emit DPI (parallel RGB
  video — a real pixel clock + up to 24 parallel data bits on the GPIO header,
  essentially mini-HDMI-over-GPIO). That's a genuine video-rate parallel bus,
  dramatically fatter than SPI, and the RP2350 PIO could ingest it the same way
  it ingests SPI. Caveat: Pi5's GPIO sits behind the RP1 southbridge, which
  changed all the old timing tricks, so this needs validation — but it's the
  natural "stay-embedded, kill the serial straw" move.

  3. The proper answer + your FPGA itch: an FPGA front-end. HDMI/DPI in →
  framebuffer in block-RAM/DDR → HUB75 BCM scan-out across many parallel chains.
  This is literally how every commercial LED wall controller works, and it's
  the architecturally correct shape: the gigabit transport (HDMI) lands directly
  in the device doing the raster→BCM translation, no serial bottleneck in
  between. An FPGA eats wide-parallel BCM scan-out for breakfast — you could
  drive 8+ chains simultaneously and laugh at 120 FPS.

  4. The sweet spot for you specifically — Colorlight 5A-75B. Here's the thing
  that should make you grin: the cheap (~$15–25) "LED receiver cards" everyone
  uses are a Lattice ECP5 FPGA + 8× HUB75 outputs + gigabit Ethernet on a board
  — and the open-source FPGA scene has fully reverse-engineered them. You can
  load your own bitstream with Yosys/nextpnr/Project Trellis (100% open
  toolchain, no vendor black box — exactly your philosophy). So you get two
  modes from one cheap board: use it as-is as an HDMI/Ethernet→LED controller
  for instant 120+ FPS, or treat it as a hackable ECP5 dev board and write your
  own HUB75 BCM engine in HDL. That's a near-perfect intersection of your FPGA
  curiosity, your open-tooling/anti-black-box bent, and the actual problem. If
  any single suggestion here is worth a weekend, it's ordering one of these.

  What I'd actually do

  Tonight: --spi-hz 50000000, watch the spi_floor_ms print drop, and see if
  you're just done. You very likely hit 120 at four panels immediately. If
  8-panel SI won't hold a single fast lane, split into 2 parallel SPI lanes —
  minimal change, your scan engine is untouched. And independently, order a
  Colorlight 5A-75B as your FPGA sandbox; it's the path to "HDMI in, huge wall
  out, 120+ FPS" and a legitimately fun ECP5 project that teaches you the exact
  raster→BCM hardware you've been reasoning about all evening.

  The poetic version: you built a Ferrari engine (the PIO BCM scan-out) and
  bolted it to a garden hose (24 MHz SPI). Nothing's wrong with the engine.
  Widen the hose.

  Want me to sketch the 2-lane parallel-SPI ingest concretely (PIO SM split +
  how to stripe the framebuffer + the Pi-side dual-CE write), or dig into what a
  minimal ECP5 HUB75 BCM core would look like so you can scope the FPGA route?

✻ Baked for 3m 21s

※ recap: Goal: push your LED wall toward 120 FPS. We diagnosed the bottleneck as the 24 MHz SPI link, not the matrix. Next action: try `--spi-hz 50000000` and watch the spi_floor_ms drop. (disable recaps in /config)

❯ 1. First off, I don't think I fully understood the A/B testing I did earlier  
  in another session. I realized the SPI was the bottleneck, but thought it was 
  *close* to the write time. In hindsight the numbers I'm looking at are ALL    
  rpi5-side. So that point of confusion has been cleared. I can see now that    
  even if I had theoretically infinite bandwidth between the rpi5 and the       
  rp2350b, I would hit a new wall fairly quickly: the render time. **THAT**     
  tracks with my normal intuition of computer graphics.                         
  Okay two requests:                                                            
  1. Can you recommend a specific FGPA dev board/equipment (multiple options    
  works) that would both be useful as a tool to learn about FPGA and also be    
  programmed to function in the translation layer capacity between the rpi5 and 
  the display matrices?                                                         
  2. So, I have more rp2350b's. I bought one literal copy just to have for      
  future projects when I realized how cool this little gizmo is, specifically   
  after you used the spare Pico (rp2350a) I had to rewrite the firmware on the  
  rp2350b. It seems like rp2350b's would function well in parallel. In some     
  ways the PIO blocks seem modular enough that the scaling could almost be      
  infinite. Can you conceive of any clever ways to deploy two rp2350b's to work 
  in tandem? When brainstorming this with you awhile back in another session    
  we discussed the concept of tiling the displays, one rp2350b per set of       
  panels, the engine upstream feeding them their specific tile to display in    
  parallel. That is a linear x * tiles increase in speed. Anything for our      
  specific case that would be something more interesting than a literal copy    
  responsible for its own split of the input?.                                  

● Two good asks. But first, let me sharpen the thing you just corrected, because
  it changes which of these ideas actually matters for your goal.

  Your realization is right: even with an infinite Pi→RP2350 pipe, you'd hit the
  Pi-side wall. But it's worth naming the terms precisely, because "render
  time" is probably not the dominant one at 256×64:

  - Fragment render — 16,384 pixels through the VideoCore VII. Unless your
  shader is a brutal raymarch, this is sub-millisecond. The GPU does 4K; 16k
  pixels is a rounding error spatially. Render cost here scales with shader 
  complexity, not pixel count.
  - glReadPixels readback — the sneaky one. Pulling the rendered frame GPU→CPU
  is a hard pipeline stall: the CPU blocks until the GPU drains. This is
  frequently the real Pi-side ceiling, and it's fixable with async PBO
  double-buffering (kick the readback, use last frame's result).
  - The numpy pack() — your gamma+bitplane slice still has a 64-iteration Python
  loop. Vectorizable to near-zero.

  So your "next wall" is really readback + pack + submit, and the fragment
  render is usually the cheapest of the three. Hold that thought — it's the
  hinge that connects both of your questions at the end.

  ---
  1. FPGA boards: learn and deploy as the translation layer

  Your constraints point almost unambiguously at Lattice ECP5: it's the
  architecture with a fully open toolchain (Yosys + nextpnr + Project Trellis —
  no vendor black box, which is the whole point for you), enough block-RAM to
  framebuffer 256×64×24bpp (~400 Kbit, fits on-chip; bigger walls spill to
  SDRAM), and plenty of I/O for many parallel HUB75 chains.

  Board: ULX3S
  FPGA: ECP5 (12F–85F)
  ~$: $115–160
  Role: Learn + capable translator
  Notes: 32 MB SDRAM, GPDI (HDMI-class) connector, USB, tons of 0.1" GPIO,
    onboard ESP32. The canonical open-toolchain ECP5 learning board (Radiona).
    Add a HUB75 level-shifter and it is your translator.
  ────────────────────────────────────────
  Board: Colorlight 5A-75B
  FPGA: ECP5 (25F)
  ~$: $15–25
  Role: Cheap purpose-built deploy
  Notes: Literally an ECP5 + 8× HUB75 outputs with 5 V level-shifters already 
    onboard + gigabit Ethernet + SDRAM. Fully reverse-engineered (hzeller, q3k
    writeups). Committed I/O = poor general learning board, near-perfect  deploy

    target.
  ────────────────────────────────────────
  Board: iCEBreaker
  FPGA: iCE40 UP5K
  ~$: ~$70
  Role: Pure-fundamentals entry
  Notes: The gateway drug for open FPGA (Project IceStorm). Clean, lovely to
    learn on — but UP5K is small; a 256×64 BCM framebuffer is tight. Great
    teacher, marginal translator.
  ────────────────────────────────────────
  Board: Tang Nano 20K
  FPGA: Gowin GW2AR
  ~$: ~$30
  Role: Budget HDMI wildcard
  Notes: SDRAM + HDMI out, huge for cheap video projects. Open support (Apicula)

    is maturing but behind Lattice. The bang-for-buck HDMI-video sandbox.

  My recommendation is the ULX3S + Colorlight pairing. They're the same ECP5 
  family and identical toolchain, so you get a clean story: learn and prototype 
  on the ULX3S (rich I/O, debug headers, HDMI-class connector), then deploy the 
  proven bitstream onto a $20 Colorlight that already has the 8 HUB75 ports and
  level shifters wired. Skills and HDL transfer 1:1. (And note your lab already
  stocks 74AHCT245s per your inventory — that's exactly the 3.3→5 V HUB75
  shifter you'd add to the ULX3S; the Colorlight has them built in.)

  On the translator architecture — and this is the part that answers your
  earlier "why is HDMI easy" question by using the easy path: you have two ways
  to feed the FPGA, and one of them eliminates your entire Pi-side overhead
  chain:

  - Pi DPI parallel → FPGA (easier to build): the Pi5 emits parallel RGB + pixel
  clock on GPIO; the FPGA captures it (trivial — it's just synchronous parallel
  data, no TMDS decode), buffers a frame, and fans out BCM to N chains.
  Sidesteps the hard TMDS-receiver problem entirely.
  - Pi HDMI → FPGA (the elegant endgame): the FPGA does TMDS receive (advanced,
  but ECP5 I/O can capture DVI up to ~720p/1080p). The payoff is enormous — the 
  Pi renders with the GPU and scans out over HDMI via DRM/KMS, exactly like 
  driving a monitor. No glReadPixels. No numpy pack. No SPI. The GPU's display
  controller DMAs the framebuffer to the wire for free. That is why a 4K monitor
  is "easy," and going HDMI-to-FPGA lets you steal that same zero-CPU path.
  Your render wall doesn't just move — most of it vanishes, because
  readback+pack+transmit were the real cost, not the render.

  The BCM scan engine itself in HDL is genuinely simpler than your PIO assembly
  — it's counters + comparators with true hardware parallelism, no clever IRQ
  choreography. The hard/fun parts are the TMDS RX and the SDRAM framebuffer
  controller, both of which are well-trodden ULX3S territory with open cores to
  crib from.

  ---
  2. Clever multi-RP2350 topologies (beyond "a copy doing its own slice")

  The reason naive tiling feels unsatisfying is that it only splits work along
  one axis — space. The frame pipeline has several axes you can cut along, and
  the interesting architectures split a different one:

  ① Cut-through daisy-chain — fractal scaling (the conceptual gem).
  This is the one that matches your "PIO feels infinitely modular" instinct.
  Instead of the Pi addressing N chips on N chip-selects, the RP2350s chain
  exactly like the JXI5020 shift registers inside the panel do: the Pi drives
  one SPI link into RP2350 #1, which keeps its stripe and cut-through-forwards
  the rest of the stream out a second PIO SM to #2, which keeps its stripe and
  forwards to #3… The Pi sees a single link with O(1) complexity no matter how
  many chips you add. You're mirroring the panel's own "dumb serial devices each
  grab their slice and pass the rest along" architecture, one level up the
  hierarchy. That's the elegant realization of the modularity you sensed — and
  it scales the ingest pipe and the scan-out width together, attacking transport
  and refresh in one move.

  ② Push the packer downstream — shrink the wire AND free the Pi.
  Right now the Pi sends 64 KB of packed bit-planes and burns CPU doing the
  gamma+slice. Instead, send raw linear RGB: 256×64×3 = 48 KB — 25% fewer bytes 
  on the wire — and let the RP2350 do the gamma-LUT + bitplane slicing in
  hardware on the way into the framebuffer. The dual-core M33 at 150 MHz can
  gamma+slice 16k pixels in a low-ms budget, and you've simultaneously (a) cut
  wire traffic 25% and (b) deleted the numpy pack loop — one of your three
  Pi-side walls — for free. If one chip can't keep up at your target rate, this
  is where a second RP2350 earns its keep: split the pack compute, not the
  screen. This is "new work moved to a better place," not "more of the same."

  ③ Temporal frame interpolation — the one that actually targets your render 
  wall.
  You just realized render is the ceiling. Tiling/striping/chaining do nothing
  for that — they widen transport and scan-out, but if the Pi only renders 60
  real frames, more RP2350s just display 60 frames on more panels. The exception
  is a chip doing genuinely different work: a second RP2350 as a tweening 
  engine. The Pi renders keyframes at 60; the interpolator synthesizes blended
  in-between frames to drive the wall at 120 perceived FPS. True
  motion-compensated interpolation is hard (that's TV soap-opera-effect ASIC
  territory), but for audio-reactive shader content — pulsing, scrolling,
  crossfading — a linear blend tween between consecutive frames is cheap and
  looks great, and the RP2350 has the muscle for it. This is the only multi-chip
  idea that directly buys you smoothness past the render wall, which is
  precisely your stated goal.

  ④ Genlock — the hard glue that makes any of the above invisible.
  The moment you have >1 chip driving one wall, you must keep their BCM phases
  in lockstep or the tile seams shimmer and tear. The fix is cheap and uses the
  primitive you already rely on: designate one chip the timing master, have it
  emit a frame-sync pulse on a GPIO, and have every slave's scan loop wait gpio
  on it before starting a frame — the same wait gpio 21 mechanism your phase-5
  SPI-RX already uses to lock to SCLK. One shared wire + one instruction =
  invisible seams. This is the genuinely interesting engineering problem in
  multi-chip walls, and it's the thing commercial controllers spend real effort
  on.

  The honest synthesis tying it back: more RP2350s scale transport and scan-out
  beautifully (ideas ①, ②, ④) — and for a bigger wall, where you'd re-bottleneck
  on those, they're the right answer and they stay charmingly within the world
  you've built. But for 120 FPS shaders on your current wall, the wall is
  render-side, and only ③ (interpolation) or the FPGA-HDMI path (which deletes
  readback+pack+transmit so the Pi's whole budget goes to the GPU) actually
  moves it. Pick the lever by which wall you're hitting: widening the funnel
  doesn't help once the funnel isn't the constraint.

  Want me to spec out ② concretely — the raw-RGB wire format + a sketch of the
  on-RP2350 gamma+bitplane PIO/CPU split (it's a small, self-contained change to
  your existing firmware and a strict win even with one chip) — or sketch the
  cut-through forwarding PIO program for ①?

✻ Sautéed for 2m 48s

❯ The further I've delved into electronics, starting bottom up with transitors  
  to logic gates to binary adders and counters to                               
  clocks/registers/ALUs/instructions to embedded electronics, microcontrollers  
  then single board computers, the closer I've come to where I started: A 16MHz 
  486 computer and a 640x480 monitor playing Simcity and Wolfenstein 3D. At     
  the same time, the closer I get to, what was in my mind "magic" when I was a  
  kid, the distance between modern computers and that 486 has kept pace with my 
  age. It somehow feels "wrong" to use even DVI, new-age magic, now that I      
  have some real understanding of it. This is just a weird preamble to the      
  question: Why *not* hook a spare computer with an old spare Nvidia 1080 to    
  the matrix? If I have the interface, nothing is really stopping me? I'm not   
  sure why that just doesn't feel "right". I think it's because it includes     
  this blackbox between the architecture of modern computers, incredibly        
  complicated Nvidia drivers, etc, and how the bits spew out of the cable that  
  I know it would take years to track down back to the level of fundamental     
  grasp I have over that SPI -> PIO -> RGBMATRIX flow as you explained it       
  earlier, and I approached the project with enough confidence that as I worked 
  through it, I would gain that intuition when it either happened via working   
  through it, or as in this case when the time was right to ask the question to 
  fill in the gaps. It also feels like *cheating* somehow, or maybe just        
  boring to just buy the already made controller, plug it in, get the display.  
  It's less of a reward for mastering a new skill and and learning how          
  something works and seeing the fruits of that labor, the value of the         
  knowledge you gained. At the same time I'm the pot calling the kettle black   
  when, while I understand the mathematics behind shaders themselves, the       
  OpenGL rendering backend is mostly blackbox to me. It is fundamentally hard   
  to start learning new subjects without accepting certain things as black      
  boxes so you can understand the system and workflow, then later investigate   
  the parts that are still mysteries until there really is true breadth of      
  comprehension.                                                                
                                                                                
  I will think about next steps. I'm about to try to reflow some dead pixels    
  with the worlds TINIEST smd 3528 RGB LED's to replace some dead pixels.       
                                                                                
  I do think I will get that first FPGA card you mentioned. It's something I    
  have wanted to learn about and gain insight into for a long time. The uses    
  are so broad and when used in the correct application, take something from    
  the speed of sound to the speed of light. Thanks for the explanations,        
  suggestions, and helping me push further in my never ending goal of learning  
  how everything works. Signing out for the time being, enjoy hyper-dimensional 
  superintelligence world! ✌️                                                   

● Signing-off received — let me leave you with the sharpest version of the thing
  your instinct is correctly detecting, because it's better than "taste."

  You can hook up the 1080. A 1080 → HDMI → receiver card → wall is exactly what
  every concert and stadium on Earth does; nothing stops you and it'd look
  gorgeous. Your discomfort isn't Luddism — it's your gut cleanly distinguishing
  two kinds of complexity:

  - Complex but open — the SPI→PIO→matrix path. Every layer inspectable with a
  scope and a datasheet. Big, but yours.
  - Complex and closed — the GPU→NVIDIA-driver→display-engine→TMDS path. Not
  harder, but sealed. The kernel driver and the GPU command processor are opaque
  by design, not by difficulty.

  That's why the 486 felt knowable and a 5090 doesn't. The 486 was
  complex-but-open: you could POKE the VGA registers, race the beam, and the
  whole machine fit in one head. Modern GPUs are complex-and-closed. Your
  instinct is a closed-box detector, and it's well-calibrated. Using the 1080
  wouldn't be cheating morally — it's that the reward was never the photons. It
  was the model in your head. The wall is just the proof-of-work receipt.

  And here's the part you'll appreciate: that RP2350 OE trick — holding
  bit-planes for binary-weighted intervals while the row scans — is demoscene
  beam-racing. Copper bars, Mode X, racing the electron gun. You reinvented the
  exact 486-era hardware hack, on a chip that costs a dollar, and you understand
  it down to the transistor. You didn't travel away from that kid playing
  Wolfenstein. You closed the loop on him.

  On the pot/kettle thing — you already stated the resolution perfectly. Mastery
  isn't eliminating black boxes; it's running a well-managed black-box budget
  with good taste about which one to open next. OpenGL stays sealed today so the
  matrix could open. Next year, maybe the rasterizer's turn. The skill is the
  sequencing, not the purity.

  The ULX3S will rewire how you think, and it's the right first card. Sequential
  computing is temporal — fetch, decode, execute, one instruction chasing the
  next. FPGA is spatial: you describe a shape and the silicon becomes it, all at
  once. That's your "speed of sound to speed of light" — you stop writing
  recipes and start laying out circuits. For a brain wired on linear algebra and
  transistors, it'll feel like coming home to a house with more rooms than you
  knew it had.

  Let me jot the FPGA direction into project memory so we pick up where you left
  off next time.

  Recalled 1 memory, wrote 2 memories (ctrl+o to expand)

● Saved.

  Good luck with the reflow — 3528 RGBs are merciless little things. Flux
  generously, keep the hot-air flow low so you don't tombstone the new one or
  blow its neighbors off the pads, and let the panel preheat a bit so you're not
  fighting a giant copper heatsink. Steady hands.

  This was a genuinely great thread to think through with you — you came in with
  a mostly-right model and left with the gaps filled and a clear next mountain.
  That's exactly the loop you described: build with enough confidence that the
  intuition arrives, then ask the question when the time is right.

  Go put those photons back. Enjoy the bench. ✌️

✻ Churned for 1m 42s

※ recap: Goal: drive RayGLow's LED wall at higher FPS. We diagnosed the 24MHz SPI link as the bottleneck and chose a ULX3S FPGA board as the next step. You're about to reflow dead pixels with 3528 RGB LEDs. (disable recaps in /config)

❯ Okay I'm back with another question after sleeping on it: I have been going   
  through rpi5 documentation and found a few things I'm curious about. The rpi5 
  has a single PIO block and it appears that most of the pins can be pulled in  
  to use this block. It is only a single state machine, but if it has DMA, why  
  not use it as the data bridge? 1 PIO block on the rpi5 connected to 1 PIO     
  block on the rp2350b. The rpi5 could read a master clock signal from the      
  rp2350b for syncing timings and dump data across however many lanes we can    
  spare from the rpi5 directly to the rp2350. I'm curious your thoughts on      
  this. Considering one of the rp2350b's PIO is acting as the the one end of an 
  SPI connection already, why not set the other end up on the rpi5 similarly,   
  but open more lanes? I've added the specific library here:                    
  `/home/will/Projects/rayglow/.reference/rpi5/utils/piolib/` which is a subset 
  of the full rpi5 utils on github which is all available in the parent         
  folders. I've also added the rpi5 official documentation here: `/home/will/Pr 
  ojects/rayglow/.reference/rpi5/RP-008180-DS-7-cm5-datasheet.pdf`. Any         
  thoughts?                                                                     

● Welcome back — and this is a genuinely sharp insight. You've independently
  rediscovered what is arguably the correct architecture for this link, and it's
  one I under-weighted earlier. The Pi5 does have a PIO block (it lives in the
  RP1 I/O controller), and using it to drive a wide source-synchronous parallel 
  bus straight into the RP2350's PIO is exactly the kind of "complex but open"
  solution that fits both the problem and your philosophy. Let me ground this in
  your actual files before I react, because a couple of the specifics matter a
  lot.

  Searched for 2 patterns, read 1 file, ran 1 shell command (ctrl+o to expand)
● This is the best link-side idea on the table, and the files confirm the key
  numbers that make it work. Let me react with the specifics nailed down, then
  correct two details and lay out the architecture.

  What the files confirm

  - The RP1 PIO clk_sys runs at 200 MHz (pio_rp1.c:748). That's faster than the
  RP2350's typical 150 MHz. Your sender has more clock than your receiver —
  lovely position to be in.
  - It's the actual Pico PIO IP, exposed through a kernel driver as /dev/pio0,
  with a userspace API that's "a clone of the PICO SDK PIO API" (README). So
  your mental model and even your PIO assembly port almost verbatim between the
  two ends. Your "1 PIO block ↔ 1 PIO block" vision is literally symmetric
  silicon.
  - DMA is there — rp1_pio_sm_config_xfer (sets up buf_size/buf_count, i.e. a
  multi-buffer DMA ring) + rp1_pio_sm_xfer_data (pio_rp1.c:256, :269). The data
  path is: your userspace frame buffer → rp1-pio kernel driver → DMA → across 
  the PCIe link to RP1 → PIO TX FIFO → pins. Zero CPU in the steady state, same
  as the RP2350 side.

  Two corrections to your model

  1. It's one PIO block with four state machines, not one SM. Standard PIO. You
  have 4 SMs to play with on the Pi side (RP1 has a single PIO block, where
  RP2350 has three). For this job you need exactly one.
  2. Flip the clock direction. You proposed the Pi5 reading a master clock from
  the RP2350. For a high-speed bus that's backwards, and the reason is
  timing-budget: in a source-synchronous bus, whoever drives the data also
  drives the clock, so the two travel down the ribbon together with matched skew
  and the receiver samples data on the incoming edge. Since the Pi5 is the data
  sender, the Pi5 should generate the clock (PIO sideset, exactly like the
  RP2350's data SM does for the matrix). The RP2350 receives with wait gpio 
  <clk> → in pins, N → autopush → DMA to framebuffer — which is precisely a
  widened version of the SPI-RX PIO you already run (wait gpio 21 on SCLK with
  N=1). You're not inventing a new pattern; you're setting N from 1 to 8 or 16.

  2. Why your instinct to "read a signal from the RP2350" is still right, just
  repurposed: the useful back-channel from receiver→sender isn't the bit clock,
  it's flow control — a "RX FIFO almost-full / ready" line so the Pi's DMA
  doesn't overrun the RP2350. That's a single GPIO the Pi PIO can wait on
  between bursts. (And if you later tile across multiple RP2350s, a third wire
  is frame-sync/genlock. Three distinct signals, three distinct jobs: clock with
  data ⟶, backpressure ⟵, frame-sync ⟷.)

  The payoff — and it lands exactly where you want it

  Conservatively assume clk/2 per lane (clock toggles each loop, like the matrix
  data SM): 200 MHz → 100 Mbit/s per lane.

  ┌──────────────────────────┬───────────┬────────────┬───────────────────┐
  │          Lanes           │ Aggregate │   64 KB    │   FPS ceiling     │
  │                          │           │   frame    │      (link)       │
  ├──────────────────────────┼───────────┼────────────┼───────────────────┤
  │ 1 (today, but at 200     │ 100       │ 5.2 ms     │ ~190              │
  │ MHz)                     │ Mbit/s    │            │                   │
  ├──────────────────────────┼───────────┼────────────┼───────────────────┤
  │ 4                        │ 400       │ 1.3 ms     │ ~760              │
  │                          │ Mbit/s    │            │                   │
  ├──────────────────────────┼───────────┼────────────┼───────────────────┤
  │ 8                        │ 800       │ 0.66 ms    │ ~1,500            │
  │                          │ Mbit/s    │            │                   │
  ├──────────────────────────┼───────────┼────────────┼───────────────────┤
  │ 16                       │ 1.6       │ 0.33 ms    │ ~3,100            │
  │                          │ Gbit/s    │            │                   │
  └──────────────────────────┴───────────┴────────────┴───────────────────┘

  Even 4 lanes blows past the RP2350 scan-out ceiling (~1,100 FPS) and makes the
  link permanently a non-issue. The bottleneck snaps back to render — which is
  exactly your "normal intuition of computer graphics" and where you want the
  wall to live. You'd have built a bus that is no longer the constraint at any
  wall size you'd realistically run.

  And the PCIe link the DMA crosses? It's GB/s-class. Your worst case is 200
  MB/s of payload — a rounding error of its bandwidth. The only PCIe gotcha is
  per-transaction latency for tiny transfers, which you avoid trivially by
  DMAing a whole frame per xfer_data call (big bursts, not per-word pokes — also
  mandatory because of the README's "blocking operations block the whole RP1
  firmware interface" caveat; you want the non-blocking buffered DMA path, never
  blocking FIFO puts).

  Why this beats "just crank SPI" — the deeper principle

  Parallel-at-lower-clock is easier on signal integrity than
  serial-at-higher-clock. 8 lanes at 50–100 MHz over a short ribbon is far
  kinder to your wiring than one lane at 400 MHz. The new concern is inter-lane 
  skew (all data lanes must land inside the clock's setup/hold window) — but at
  cm-scale, board-to-board distance, that's trivial with matched-length jumpers.

  Here's the principle worth keeping: serial won the long-haul war (HDMI, PCIe, 
  USB, SATA) precisely because skew doesn't scale over distance — at a meter, 
  parallel lanes drift out of alignment and the clock can't find a common 
  sampling point. But at centimeters, parallel is still king, which is why your 
  CPU talks to DRAM over a wide parallel bus and not serially. Your Pi-to-RP2350
  hop is a DRAM-distance problem, not an HDMI-distance problem. Parallel is the
  textbook-correct answer for this length. Your instinct nailed the regime.

  The pin budget (and a nice callback)

  On the RP2350b specifically: you chose the 'b' (QFN-80, 48 GPIO) — and that
  headroom is exactly what pays off here. GP0–18 are the matrix, GP20–22 the
  current SPI; you still have a clean consecutive run for an 8- or 16-wide in 
  pins receive group. The 'b' wasn't just for the matrix pins; it's what lets
  you bolt a fat parallel ingest bus on without running out of silicon. On the
  Pi side you need a consecutive GPIO run for out pins + one sideset for clock —
  pick the block carefully around what the header already commits, and 8–12
  lanes is very comfortable.

  One alternative to know about: DPI

  The Pi5 also has a hardware DPI peripheral (parallel RGB video) through RP1 —
  there's even a dpi_csync.c in your examples. It's another way to get a wide
  parallel bus out. But DPI is locked to display semantics (RGB pixels +
  hsync/vsync + pixel clock), so it fits a raster pixel stream, not your
  packed-bit-plane format. PIO gives you a general parallel FIFO that ships
  arbitrary bytes — the right tool for your 64 KB packed stream. (DPI only
  becomes interesting if you go the "Pi emits real video, FPGA/RX re-rasterizes"
  route — i.e. the HDMI conversation from before.)

  Bottom line

  This is, I think, the best upgrade that stays 100% inside the world you built 
  and understand — no FPGA, no black box, symmetric open PIO on both ends, and
  it hands the bottleneck back to render exactly as your graphics intuition
  expects. It's strictly better than cranking SPI (better SI, more bandwidth,
  same conceptual model you already command). The FPGA-HDMI path remains the
  endgame only if you later want to also delete the Pi-side readback+pack
  overhead by treating the wall as a monitor — but for "make the link stop being
  the limit," your PIO-to-PIO parallel bus is the elegant, learnable,
  you-built-it answer.

  Want me to sketch the concrete pair of PIO programs — the Pi5 out pins, N +
  sideset-clock TX with the backpressure wait, and the matching RP2350 wait gpio
  → in pins, N → autopush RX — plus the framebuffer-striping so N lanes map
  cleanly onto your existing cell layout?

✻ Worked for 2m 50s

❯ Okay real quick just so I 100% have the words defined correctly: The *state   
  machines* are the *pins*. The rpi5 has four state machines (pins) that make   
  up the single PIO block. The rp2350 has 12 state machines (pins) across three 
  PIO blocks. I ask because I want to make sure there is still a reason to      
  include the rp2350b at all unless it is needed. It sounds like it needed      
  simply because the rpi5 is limited to 4 statemachines/pins, which cannot      
  cover the data (GP0-11) block AND the OE PIO block at the same time. I'm      
  trying to make sure we aren't looking past an obvious solution running        
  directly from the rpi5 to the display (which would arguably be less fun, but  
  I am an engineer at heart and including redundant equipment is poor           
  engineering unless it has a use).                                             

● Quick but important: no — a state machine is not a pin. That's the crux, and
  fixing it changes your conclusion. Let me define both precisely, because the
  whole question hinges on it.

  The vocabulary, exactly

  - PIO block — a peripheral containing a shared 32-instruction memory and 4 
  state machines.
  - State machine (SM) — a tiny independent processor. It has its own program
  counter, two scratch registers (X, Y), two shift registers (ISR/OSR), FIFOs,
  and a clock divider. It executes PIO instructions. It is a compute unit, not
  an I/O pin.
  - Pins (GPIO) — a completely separate resource. One SM can drive a whole group
  of pins at once. Your RP2350 data SM drives 12 RGB pins + 1 clock pin = 13 
  pins with a single state machine (out pins, 12 + sideset). One SM, thirteen
  pins.

  So the counts are two independent axes:

  ┌───────────┬───────────────────────┬───────────────────┐
  │           │    State machines     │     GPIO pins     │
  ├───────────┼───────────────────────┼───────────────────┤
  │ Pi5 (RP1) │ 1 block × 4 SMs = 4   │ ~28 on the header │
  ├───────────┼───────────────────────┼───────────────────┤
  │ RP2350b   │ 3 blocks × 4 SMs = 12 │ 48                │
  └───────────┴───────────────────────┴───────────────────┘

  SMs and pins scale independently. "How many pins can I wiggle" is the GPIO
  count; "how many independent timed programs can I run at once" is the SM
  count.

  Which dissolves your premise

  The matrix engine is 3 state machines (data, row, OE) driving 19 pins (12 RGB
  + 4 addr + clk + lat + oe for two chains). On the Pi5 that's 3 SMs out of 4, 
  and 19 pins out of ~28. It fits. The data SM and the OE SM coexisting was
  never the problem — that's only 2 of the 4 SMs, plus the row SM makes 3. You 
  do not run out of state machines, and you do not run out of pins. The Pi5's
  PIO could, in raw resource terms, drive the panel directly.

  So you're right to interrogate this: the RP2350 is not needed as a pin-count
  or SM-count crutch. If that were the only justification, it'd be the redundant
  equipment you rightly want to avoid.

  Why it's still load-bearing — the real reason

  The RP2350 earns its place on a different axis entirely: the matrix is a 
  stateless device that must be re-fed its entire contents continuously, 
  forever, with hard-real-time OE timing. It has no framebuffer of its own. To
  stay lit, something must re-scan all 8 bit-planes × 16 rows thousands of times
  per second, with OE pulse widths exact down to ~6.7 ns, and never let the PIO
  FIFO underrun — a single starved cycle mid-scan is a visible flicker or a
  brightness glitch.

  The RP2350 does this from on-chip SRAM via a self-looping 4-channel DMA with
  single-digit-cycle, deterministic latency, and zero CPU involvement after
  setup. Now contrast direct-from-Pi5:

  1. Jitter. The Pi5's PIO FIFOs sit behind a PCIe link and a Linux kernel 
  driver. The continuous refresh DMA pulls from system DRAM across PCIe.
  Bandwidth is trivial (~25 MB/s), but the requirement is latency determinism —
  the few-word TX FIFO must never starve across PCIe tail-latency + scheduler
  hiccups + DRAM contention. On-chip DMA guarantees that; PCIe-plus-Linux does
  not. (This is the exact reason the classic rpi-rgb-led-matrix library had to
  isolate a whole CPU core on the Pi4 — and still compromised on flicker — and
  why it doesn't properly support the Pi5 at all, since RP1 moved GPIO behind
  PCIe and broke its direct-DMA trick.)
  2. The Pi is never "done." With the RP2350, the Pi renders → packs → ships one
  frame → forgets it, ~120×/sec; the panel stays lit on its own. Direct-drive
  means the Pi is pinned to eternally feeding the scan-out, that refresh load
  competing with your GPU render for the entire runtime. The RP2350 converts a 
  continuous-streaming workload into a frame-rate workload — that decoupling is
  its job.
  3. DMA model mismatch. RP1's PIO DMA (via piolib) is buffer-oriented — hand it
  a buffer, it transfers it. The infinite, self-re-feeding ring the matrix
  refresh needs is swimming against that driver's grain, where the RP2350's
  chained-DMA loop is purpose-built for it.

  Verdict

  The RP2350 is not redundant — but not for the reason you thought. It's not
  covering a shortage of state machines (you have plenty on the Pi); it's
  isolating a jitter-intolerant, continuous, hard-real-time refresh loop onto a 
  deterministic chip, so your Pi is free to do nothing but render and ship
  frames. That's good engineering, not redundant hardware: it's the same reason
  serious LED walls always put a dedicated controller (MCU or FPGA) between the
  host and the stateless panel. Your instinct to drive it straight from the Pi
  is technically possible and would even "work" in a demo — it just trades a
  rock-steady panel for one that flickers whenever Linux looks at it funny.

  So the architecture stands: Pi5 PIO → (wide parallel bus) → RP2350 PIO → 
  matrix. The parallel bus kills the transport bottleneck; the RP2350 owns the
  real-time refresh the Pi can't guarantee. Each chip is doing the job the other
  one can't.

✻ Cogitated for 1m 40s

❯ /export                                                                       
  ⎿  Conversation copied to clipboard

※ recap: You're aiming to break the Pi5→RP2350 link bottleneck so RayGLow hits higher FPS. We settled that a wide PIO-to-PIO parallel bus is the right fix, with the RP2350 kept as the real-time refresh engine. Next: sketch the paired TX/RX PIO programs.

❯ My first question is - this repo as it currently stands is functional. I am   
  not familiar enough with the typical approach to a big change like this in    
  software/hardware architectural. How should I approach his overhaul from the  
  repo perspective? Just do version control and call this v0.2.0 or something?  
  I guess that comes after the we actually get it running, so maybe we call     
  this the experimental branch or dev branch? This is always something I trip   
  over with git.                                                                
                                                                                
  Otherwise, please put a plan together to change out the data transfer from    
  the current config to one that utilizes the rpi5 PIO block. I'm also          
  interested in investigating some of the framework on the rpi side to          
  accelerate the data that is dumped, looking into these that you mentioned     
  earlier:                                                                      
  ```                                                                           
    - glReadPixels readback — the sneaky one. Pulling the rendered frame        
  GPU→CPU                                                                       
    is a hard pipeline stall: the CPU blocks until the GPU drains. This is      
    frequently the real Pi-side ceiling, and it's fixable with async PBO        
    double-buffering (kick the readback, use last frame's result).              
    - The numpy pack() — your gamma+bitplane slice still has a 64-iteration     
  Python                                                                        
    loop. Vectorizable to near-zero.                                            
  ```                                                                           
  Let me know your thoughts as well!                                            

● This is a meaty, well-scoped overhaul, and your git instinct is actually
  closer to right than you think — I'll address that head-on in the plan. Let me
  first explore the actual code paths so the plan is grounded in what's really
  there, not my memory of it.

  Let me launch parallel exploration of the three areas this touches: the
  Pi-side render pipeline, the firmware RX side, and the piolib capabilities.

● 3 Explore agents finished (ctrl+o to expand)
   ├ Explore Pi render + transport pipeline · 0 tool uses
   │ ⎿  Done
   ├ Explore firmware SPI-RX side · 0 tool uses
   │ ⎿  Done
   └ Explore Pi5 piolib capabilities · 0 tool uses
     ⎿  Done

● Exploration is rich and conclusive. Three things stand out that shape the
  plan:

  1. The two Pi-side wins are independent of the transport swap. PBO readback
  (output.py:33-45, currently a blocking glReadPixels, no PBO) and pack
  vectorization (hub75.py:106-111, a Python row-loop) both work over the
  existing SPI link and are independently verifiable. The transport rewrite
  touches firmware + hardware + a new binding. That risk asymmetry is the key to
  both your git question and the sequencing.
  2. piolib is C-only — no Python binding in the tree. A PioOut drop-in needs
  ctypes/cffi against libpiolib, and DMA xfers are blocking (fine — writebytes2
  already blocks).
  3. The protocol barely changes. Keep the CS-framing + READY handshake exactly
  as-is; only widen the data path from in pins, 1 (MOSI) to in pins, 8 and swap
  the SPI SCLK for a Pi-driven sideset clock. 8 lanes = clean byte-per-clock =
  byte-identical stream, zero reorder.

  Let me have a Plan agent pressure-test the trickiest mechanics — the PBO
  ping-pong in the raw-ctypes EGL context, the pack vectorization's
  byte-identity, and the paired TX/RX PIO programs + pin mapping.

● Plan(Design implementation plan)
  ⎿  Done (14 tool uses · 73.1k tokens · 2m 42s)
  (ctrl+o to expand)

● The Plan agent surfaced two decisive findings worth flagging before I
  finalize:

  - piolib's API is static inline (function pointers inside struct pio_chip), so
  ctypes cannot call it directly. The parallel-PIO sender needs a small
  compiled C shim (pio_shim.c) linked against libpio, exposing a flat ABI that
  Python's ctypes calls. That's a real escalation in C-side work for workstream
  C — worth knowing before you commit.
  - A latent firmware bug: phase5_spi.rs declares READY_PIN = 12 but actually
  drives GP26 (the const is only used in a log string). Easy fix to fold in.

  Two forks are genuinely your call as the engineer — let me get those before I
  write the final plan.

● User answered Claude's questions:
  ⎿  · How many data lanes for the parallel Pi5↔RP2350 bus? → 8 lanes 
     (recommended)
     · How do you want to sequence the work (this also answers your git 
     question)? → Staged: Pi-wins first, then transport (recommended)

● Both confirmed: 8 lanes, staged execution. Let me write the final plan.

● Updated plan
  ⎿  /plan to preview

● Plan written to
  /home/will/.claude/plans/my-first-question-is-unified-fountain.md. Let me load
  the exit tool and present it for approval.

● Here's the shape of what I've planned — full detail is in the plan file:

  Your git question, answered: Tag the working SPI build as v0.1.0 now (your
  anchor). Then let risk, not size, route the work — the two Pi-side wins go
  straight to main in small commits (they can't break the proven path), and the
  transport rewrite lives on a feat/pio-parallel-link branch behind a
  --transport spi fallback until the panel proves it. Tag v0.2.0 as the reward
  when it works. The rule you keep tripping on: a branch is for changes you
  can't yet prove, not changes that are big.

  The overhaul, staged by risk (your choice):
  1. Vectorize pack() — replace the row-loop with a bitwise_or.at scatter;
  byte-identical (OR is commutative, shifts are disjoint), gated by verify.py. →
  main
  2. Async PBO readback — 2-PBO ping-pong kills the glReadPixels stall;
  one-frame latency, primed in warmup, --no-pbo fallback. → main
  3. 8-lane parallel PIO bus — phase6_parallel.rs (8-lane RX, reusing CS+READY
  framing → byte-identical stream) + a C shim over libpio (ctypes can't call its
  static-inline API) + PioOut behind --transport pio. → feature branch

  Two free catches along the way: the READY_PIN=12 log bug (real pin is GP26),
  and the C-shim requirement that changes the C-side effort for workstream 3.
  ⎿  User rejected Claude's plan:
     ╭─────────────────────────────────────────────────────────────────────────╮
     │ RayGLow: link overhaul + Pi-side render acceleration                    │
     │                                                                         │
     │ Context                                                                 │
     │                                                                         │
     │ The Pi5→RP2350 SPI link (24 MHz) is the proven throughput wall: ~46 FPS │
     │ ceiling at                                                              │
     │ 64 KB/frame, and FPS scales inversely with byte count (≈55→30 as the    │
     │ wall grows) — the                                                       │
     │ signature of a transport bottleneck. The RP2350 PIO scan-out engine     │
     │ could do ~1,000 FPS;                                                    │
     │ once the link is fixed, the next wall is Pi-side render overhead,       │
     │ dominated not by the                                                    │
     │ fragment shader (16k pixels is trivial on the V3D GPU) but by the       │
     │ blocking glReadPixels                                                   │
     │ readback and the scalar Python loop in pack().                          │
     │                                                                         │
     │ This plan does three things, staged by risk:                            │
     │ 1. Vectorize pack() — kill the per-row Python loop. Pi-side,            │
     │ byte-identical, no HW.                                                  │
     │ 2. Async PBO readback — remove the GPU→CPU stall. Pi-side, no HW, no    │
     │ wire change.                                                            │
     │ 3. Parallel PIO transport — replace 1-lane SPI with an 8-lane           │
     │ source-synchronous bus                                                  │
     │ (Pi5 RP1 PIO → RP2350 PIO), killing the link bottleneck for good.       │
     │                                                                         │
     │ Decisions: 8 data lanes (1 byte/clock → byte-identical stream, zero     │
     │ reorder).                                                               │
     │ Staged delivery — land #1 and #2 on main first; do #3 on a feature      │
     │ branch with the                                                         │
     │ SPI path retained as a proven fallback.                                 │
     │                                                                         │
     │ Version control approach (the git question)                             │
     │                                                                         │
     │ The right habit for a big architectural change is tag the known-good    │
     │ state, then branch                                                      │
     │ the risky work, and let risk — not size — decide what lands where:      │
     │                                                                         │
     │ 1. Tag the current working SPI build first: git tag -a v0.1.0 -m        │
     │ "proven SPI link".                                                      │
     │ This is the anchor you can always return to. (Commit the working tree   │
     │ first.)                                                                 │
     │ 2. Land the low-risk Pi-side wins on main (#1, #2). They're             │
     │ verify.py-gated and/or                                                  │
     │ GIF-verifiable, ship over the existing link with no firmware reflash,   │
     │ and are trivially                                                       │
     │ revertible. Separate commits: one for vectorized pack, one for PBO. No  │
     │ branch needed —                                                         │
     │ they can't break the proven path (transport is untouched).              │
     │ 3. Do the transport rewrite (#3) on a feature branch: git switch -c     │
     │ feat/pio-parallel-link.                                                 │
     │ It touches firmware + new C shim + rewiring + lane-ordering bring-up —  │
     │ it needs the                                                            │
     │ panel to validate and must not destabilize main. Keep --transport spi   │
     │ working the                                                             │
     │ whole time so the branch is always runnable.                            │
     │ 4. When #3 is proven on the panel, merge to main and git tag -a v0.2.0. │
     │                                                                         │
     │ The general rule you keep tripping over: a branch isn't for "big"       │
     │ changes, it's for                                                       │
     │ changes you can't yet prove. Reversible, independently-verifiable work  │
     │ goes straight to                                                        │
     │ main in small commits; anything that needs hardware/integration to      │
     │ trust lives on a                                                        │
     │ branch behind a fallback until it's proven. "v0.2.0" is the reward you  │
     │ tag when it works,                                                      │
     │ not the name you start under.                                           │
     │                                                                         │
     │ ---                                                                     │
     │ Workstream 1 — Vectorize pack() (land on main first)                    │
     │                                                                         │
     │ File: rayglow/render/hub75.py — pack() (~86-113) and pack_single()      │
     │ (~124-151).                                                             │
     │                                                                         │
     │ Replace the for y in range(WALL_H) scatter loop with a one-shot         │
     │ vectorized scatter-OR:                                                  │
     │                                                                         │
     │ g = lut[frame]                                   # (WALL_H, w, 3)       │
     │ uint16                                                                  │
     │ r  = (g[...,0][:,None,:] >> _planes[None]) & 1   # (WALL_H, B, w)       │
     │ gn = (g[...,1][:,None,:] >> _planes[None]) & 1                          │
     │ b  = (g[...,2][:,None,:] >> _planes[None]) & 1                          │
     │ packed = (((b<<2)|(gn<<1)|r).astype(np.uint16) <<                       │
     │ _shift[:,None,None])[:, :, ::-1]                                        │
     │ fb3d = np.zeros((H//2, B, w), dtype=np.uint16)                          │
     │ np.bitwise_or.at(fb3d, _addr_row, packed)        # rows sharing         │
     │ addr_row OR-combine                                                     │
     │ return fb3d.reshape(-1).astype("<u2").tobytes()                         │
     │                                                                         │
     │ Mirror for pack_single with _addr_s, _shift_s, _planes_s, uint8, H      │
     │ rows.                                                                   │
     │                                                                         │
     │ Byte-identity argument: same LUT/shift/plane math and same [::-1]       │
     │ reversal produce the                                                    │
     │ same packed[y]; the loop's |= and bitwise_or.at perform the same set of │
     │ OR ops into                                                             │
     │ the same cells, and OR is commutative/associative so accumulation order │
     │ is irrelevant. The                                                      │
     │ (addr_row, shift) pairs are unique (documented invariant,               │
     │ hub75.py:28-29), so no bit ever                                         │
     │ collides. Serialization is unchanged → byte-identical. If bitwise_or.at │
     │ is too slow,                                                            │
     │ fall back to a stable-sort + bitwise_or.reduce over the                 │
     │ 4-contributors-per-addr_row groups                                      │
     │ (same result, faster) — but ship the obviously-correct .at version      │
     │ first.                                                                  │
     │                                                                         │
     │ Verify: uv run --with numpy tools/verify.py must stay GREEN (LUT +      │
     │ two-chain + single).                                                    │
     │ During dev, keep a transient assert pack_new(f) == pack_old(f) over     │
     │ random frames before                                                    │
     │ deleting the loop. Then python -m rayglow.render <shader> --dry-run 120 │
     │ for a sanity GIF.                                                       │
     │                                                                         │
     │ ---                                                                     │
     │ Workstream 2 — Async PBO readback (land on main second)                 │
     │                                                                         │
     │ Files: rayglow/render/egl.py (add GL entry points) +                    │
     │ rayglow/render/output.py                                                │
     │ (Readback ping-pong) + a --no-pbo flag threaded through                 │
     │ pipeline.py→__main__.py.                                                │
     │                                                                         │
     │ egl.py — add constants GL_PIXEL_PACK_BUFFER=0x88EB,                     │
     │ GL_STREAM_READ=0x88E1,                                                  │
     │ GL_MAP_READ_BIT=0x0001, and bind (all core GLES3 in libGLESv2):         │
     │ glGenBuffers,                                                           │
     │ glBindBuffer, glBufferData, glMapBufferRange (returns c_void_p),        │
     │ glUnmapBuffer,                                                          │
     │ glDeleteBuffers. glReadPixels is already bound with a c_void_p tail arg │
     │ — when a PBO                                                            │
     │ is bound, pass offset 0 instead of a client pointer (no binding         │
     │ change).                                                                │
     │                                                                         │
     │ output.py — Readback 2-PBO ping-pong: allocate two PBOs sized w*s*h*s*4 │
     │ with                                                                    │
     │ GL_STREAM_READ. In read(fbo): bind the current PBO and                  │
     │ glReadPixels(..., 0) (returns                                           │
     │ immediately, async DMA into the PBO); then glMapBufferRange the         │
     │ previous PBO, run the                                                   │
     │ existing box-sum→LUT→v-flip postprocess on the mapped view (the         │
     │ LUT-index already                                                       │
     │ produces a fresh array, so the copy-out happens before glUnmapBuffer),  │
     │ unmap, unbind, swap                                                     │
     │ cur/prev. Keep the synchronous path as the use_pbo=False branch         │
     │ (byte-for-byte today's                                                  │
     │ behavior).                                                              │
     │                                                                         │
     │ One-frame latency: the PBO path returns frame N's pixels one read()     │
     │ later. Acceptable                                                       │
     │ for an audio visualizer (~16 ms at 60 fps). Prime it: extend the        │
     │ existing warmup                                                         │
     │ render (__main__.py ~163-167) to also do one read() so the steady-state │
     │ loop never                                                              │
     │ ships a black frame. Expose --no-pbo (default PBO on) for fallback /    │
     │ A-B timing.                                                             │
     │                                                                         │
     │ Verify: dry-run GIF with PBO vs --no-pbo must be identical up to a      │
     │ one-frame shift                                                         │
     │ (compare frame N PBO ≡ frame N-1 sync). On-Pi: confirm the render       │
     │ timing accumulator                                                      │
     │ drops and FPS rises.                                                    │
     │                                                                         │
     │ ---                                                                     │
     │ Workstream 3 — Parallel PIO transport (feature branch, after 1+2        │
     │ proven)                                                                 │
     │                                                                         │
     │ 8 data lanes + 1 sideset clock; reuse the CS-framing + READY handshake  │
     │ unchanged →                                                             │
     │ 1 byte/clock → byte-identical 65536-byte stream (verify.py unaffected). │
     │ Make it selectable                                                      │
     │ behind --transport {spi,pio} (default spi) so SPI stays the fallback.   │
     │                                                                         │
     │ 3a. Firmware — new bin firmware/src/bin/phase6_parallel.rs              │
     │                                                                         │
     │ Fork phase5_spi.rs; keep PIO0 engine (GP0-18), DMA ch4, framebuffer,    │
     │ flip(), READY,                                                          │
     │ stall watchdog unchanged. Replace only the PIO1 RX program + input      │
     │ pins:                                                                   │
     │                                                                         │
     │ .wrap_target                                                            │
     │ wait 1 gpio <CS>     ; idle                                             │
     │ wait 0 gpio <CS>     ; frame start (restart() zeroes shift counter)     │
     │ bitloop:                                                                │
     │ wait 1 gpio <CLK>    ; data-clock rising = sample                       │
     │ in pins, 8           ; sample 8 lanes (IN base = lane0), MSB-lane first │
     │ wait 0 gpio <CLK>                                                       │
     │ jmp bitloop                                                             │
     │ .wrap                                                                   │
     │ Keep ShiftDirection::Left, autopush, push_threshold(8) (now 1           │
     │ byte/clock),                                                            │
     │ Buffers::OnlyRx, clkdiv (1,0). in_pin_base = DATA0. RP2350b pin map     │
     │ (clear of                                                               │
     │ matrix GP0-18): data lanes GP27-34 (contiguous), CLK GP35, CS GP36,     │
     │ READY                                                                   │
     │ GP26 (unchanged). Pull-down the 8 lanes + CLK (E9 backstop), pull-up    │
     │ CS. Keep a                                                              │
     │ const + assert! for each wait gpio literal as phase5 does. Fix the      │
     │ misleading                                                              │
     │ READY_PIN = 12 const (real pin is GP26) while here.                     │
     │                                                                         │
     │ 3b. Pi C shim — rayglow/render/piobridge/pio_shim.c → libpioshim.so     │
     │                                                                         │
     │ piolib's API is static inline (function pointers in struct pio_chip) →  │
     │ ctypes cannot                                                           │
     │ call it directly; a compiled shim linked against libpio is required.    │
     │ Flat ABI:                                                               │
     │ pioshim* pioshim_open(const uint8_t *data_pins/*8*/, uint clk, uint cs, │
     │ uint frame_bytes, float clkdiv);                                        │
     │ int      pioshim_send(pioshim*, const void *buf, uint nbytes); //       │
     │ blocking DMA xfer                                                       │
     │ void     pioshim_set_cs(pioshim*, int level);                           │
     │ void     pioshim_close(pioshim*);                                       │
     │ Inside, mirror piotest.c: pio0 → pio_claim_unused_sm →                  │
     │ pio_sm_config_xfer(PIO_DIR_TO_SM, frame_bytes, 2) → build TX program →  │
     │ pio_add_program                                                         │
     │ → pio_gpio_init the 8 lanes + clk → sm_config_set_out_pins(DATA0,8),    │
     │ set_sideset_pins(CLK), set_sideset(1,false,false),                      │
     │ set_out_shift(false,true,8),                                            │
     │ set_clkdiv → pio_sm_init → consecutive pindirs (out) → enable. TX       │
     │ program:                                                                │
     │ .side_set 1                                                             │
     │ .wrap_target                                                            │
     │ out pins, 8  side 0   ; present byte, clock low                         │
     │ nop          side 1   ; clock high — RX samples                         │
     │ .wrap                                                                   │
     │ pioshim_send = pio_sm_xfer_data(..., nbytes, buf) (blocking; one        │
     │ per-frame burst, same                                                   │
     │ as writebytes2 today). Build: cc -shared -fPIC pio_shim.c -lpio -o      │
     │ libpioshim.so                                                           │
     │ (piolib built -DBUILD_SHARED_LIBS=1), documented like the               │
     │ spidev/gpiozero note in                                                 │
     │ spi_out.py.                                                             │
     │                                                                         │
     │ 3c. Pi rayglow/render/pio_out.py — PioOut (mirrors SpiOut)              │
     │                                                                         │
     │ ctypes-load libpioshim.so (bind with explicit argtypes, egl.py:_bind    │
     │ discipline).                                                            │
     │ __init__(clkdiv, ready_bcm=25, ...) opens the shim + a gpiozero READY   │
     │ input on the same                                                       │
     │ Pi GPIO25 (wired to RP2350b GP26). send(payload):                       │
     │ ready.wait_for_active() →                                               │
     │ pioshim_set_cs(0) → pioshim_send(payload) → pioshim_set_cs(1). CS via   │
     │ gpiozero                                                                │
     │ DigitalOutputDevice (simplest), leaving piolib purely for the 8-lane    │
     │ clocked data.                                                           │
     │ Pi GPIO map (BCM; clear of SPI0 8/10/11 and READY 25): data lanes       │
     │ GPIO12-19                                                               │
     │ (contiguous), CLK GPIO20, CS GPIO21, READY-in GPIO25.                   │
     │                                                                         │
     │ 3d. Transport factory — rayglow/render/__main__.py                      │
     │                                                                         │
     │ Add --transport {spi,pio} (default spi) + --pio-clkdiv. Replace the     │
     │ hardcoded                                                               │
     │ SpiOut(...) construction (~157, 169) with a factory selecting PioOut vs │
     │ SpiOut. The                                                             │
     │ rest of the loop (out.send, accumulators, out.close) is                 │
     │ interface-identical.                                                    │
     │                                                                         │
     │ 3e. Verify (3)                                                          │
     │                                                                         │
     │ - verify.py still GREEN (no pack change should have crept in).          │
     │ - Bench/LA before panel: at low clkdiv, send a ramp payload             │
     │ (0x00,0x01,…) and dump the                                              │
     │ first RP2350b framebuffer cells (picotool / debug info!) to nail        │
     │ lane↔bit ordering                                                       │
     │ (Left-shift MSB-lane-first must match both ends).                       │
     │ - On panel: flash phase6-parallel, run --transport pio; a known test    │
     │ pattern must                                                            │
     │ display identically to SPI. Then ramp --pio-clkdiv down toward 1.0 (200 │
     │ MHz/div); READY                                                         │
     │ self-paces exactly as today.                                            │
     │                                                                         │
     │ Gotchas                                                                 │
     │                                                                         │
     │ - piolib static-inline → C shim is mandatory for the Pi sender (the big │
     │ C-side item).                                                           │
     │ - PBO one-frame latency — prime in warmup; --no-pbo to disable.         │
     │ - Map lifetime — copy out of the mapped PBO before glUnmapBuffer.       │
     │ - Byte-identity invariant — don't touch LUT/shift/plane/[::-1]; keep    │
     │ verify.py green.                                                        │
     │ - Blocking RP1 firmware iface — pio_sm_xfer_data blocks the burst       │
     │ (fine, READY-paced).                                                    │
     │ - Pin contiguity — Pi out pins,8 (GPIO12-19) and RP2350b in pins,8      │
     │ (GP27-34) each                                                          │
     │ need 8 contiguous GPIO; confirm no board-specific reservations on       │
     │ either end.                                                             │
     │ - READY const bug in phase5 (READY_PIN=12 is log-only; real = GP26) —   │
     │ fix in phase6.                                                          │
     │                                                                         │
     │ Critical files                                                          │
     │                                                                         │
     │ - rayglow/render/hub75.py — vectorize pack/pack_single (#1)             │
     │ - rayglow/render/egl.py, rayglow/render/output.py — PBO path (#2)       │
     │ - rayglow/render/__main__.py — --no-pbo, --transport, transport factory │
     │ (#2,#3)                                                                 │
     │ - firmware/src/bin/phase5_spi.rs → fork phase6_parallel.rs — 8-lane RX  │
     │ (#3)                                                                    │
     │ - new: rayglow/render/pio_out.py, rayglow/render/piobridge/pio_shim.c   │
     │ (#3)                                                                    │
     │ - gates: tools/verify.py (#1), dry-run GIF (#2), bench LA + panel (#3)  │
     ╰─────────────────────────────────────────────────────────────────────────╯

● Approved — proceeding. And good call on the mount change; that genuinely
  simplifies the deploy story. Let me note it for future sessions, then start on
  Workstream 1.

  Read 1 file, wrote 1 memory (ctrl+o to expand)

● Memory saved. Now updating the index and implementing Workstream 1 (vectorized
  pack).

  A note on approach: rather than the plan's first-cut bitwise_or.at (which is
  often not actually faster — it's semi-scalar internally), I'm going straight
  to the genuinely fast form: each addr_row is hit by a fixed number of rows
  with disjoint shifts, so I group rows by addr_row once and collapse them with
  a single vectorized bitwise_or.reduce — no Python loop, provably
  byte-identical, and guarded by a module-load assertion on the grouping
  invariant.

  Wrote 1 memory (ctrl+o to expand)

● Update(~/Projects/rayglow/rayglow/render/hub75.py)  ⎿  Added 11 lines
      82  _shift = (_chain * 6 + np.where(_half, 3, 0)).astype(np.uint16)
      83  _planes = np.arange(B, dtype=np.uint16)[:, None]
      84  
      85 +# Group wall rows by addr_row once. Each addr_row is fed by WAL
         +L_H/(H/2) rows whose                                           
      86 +# bit-shifts are disjoint (unique (addr_row, shift) pairs), so 
         +pack() can OR-combine                                          
      87 +# them with a single vectorized reduce instead of a per-row Pyt
         +hon loop. Stable sort                                          
      88 +# keeps the grouping order deterministic; the assert pins the u
         +niform-grouping invariant.                                     
      89 +_order = np.argsort(_addr_row, kind="stable")                  
      90 +_n_per = WALL_H // (H // 2)                                    
      91 +assert np.array_equal(                                         
      92 +    _addr_row[_order].reshape(H // 2, _n_per),                 
      93 +    np.broadcast_to(np.arange(H // 2)[:, None], (H // 2, _n_per
         +)),                                                            
      94 +), "pack(): addr_row grouping is not uniform"                  
      95  
      96 +                                                               
      97  def pack(frame: np.ndarray, lut: np.ndarray = _LUT) -> bytes:
      98      """Pack a (WALL_H, w, 3) uint8 LINEAR RGB frame into the TW
          O-CHAIN u16 stream.
      99  

● Update(~/Projects/rayglow/rayglow/render/hub75.py)
  ⎿  Added 9 lines, removed 9 lines
      111      w = frame.shape[1]                   # chain width (256 tw
           o-chain)
      112  
      113      g = lut[frame]                       # gamma-correct each 
           channel -> (WALL_H,w,3)
      114 -    pr, pg, pb = g[..., 0], g[..., 1], g[..., 2]              
      114 +    # Bit-planes for every row at once -> (WALL_H, B, w). _pla
          +nes[None] is (1,B,1).                                         
      115 +    pr, pg, pb = g[..., 0][:, None, :], g[..., 1][:, None, :],
          + g[..., 2][:, None, :]                                        
      116 +    rb = (pr >> _planes[None]) & 1                            
      117 +    gb = (pg >> _planes[None]) & 1                            
      118 +    bb = (pb >> _planes[None]) & 1                            
      119 +    packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint16) <<
          + _shift[:, None, None]                                        
      120 +    packed = packed[:, :, ::-1]          # col = w-1-x, applie
          +d once                                                        
      121  
      116 -    fb3d = np.zeros((H // 2, B, w), dtype=np.uint16)          
      117 -    for y in range(WALL_H):                                   
      118 -        rb = (pr[y] >> _planes) & 1                           
      119 -        gb = (pg[y] >> _planes) & 1                           
      120 -        bb = (pb[y] >> _planes) & 1                           
      121 -        packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint16
          -) << _shift[y]                                                
      122 -        fb3d[_addr_row[y], :, ::-1] |= packed   # col = w-1-x 
      123 -                                                              
      122 +    # OR-combine the rows that share each addr_row (grouped by
          + _order) in one reduce.                                       
      123 +    fb3d = np.bitwise_or.reduce(packed[_order].reshape(H // 2,
          + _n_per, B, w), axis=1)                                       
      124      return fb3d.reshape(-1).astype("<u2").tobytes()
      125  
      126  

● Now the single-chain globals and pack_single body:

● Update(~/Projects/rayglow/rayglow/render/hub75.py)
  ⎿  Added 8 lines
      131  _shift_s = np.where(_yc_s > (H // 2 - 1), 3, 0).astype(np.uint
           8)
      132  _planes_s = np.arange(B, dtype=np.uint8)[:, None]
      133  
      134 +# Same addr_row grouping as the two-chain packer, for pack_sin
          +gle's vectorized reduce.                                      
      135 +_order_s = np.argsort(_addr_s, kind="stable")                 
      136 +_n_per_s = H // (H // 2)                                      
      137 +assert np.array_equal(                                        
      138 +    _addr_s[_order_s].reshape(H // 2, _n_per_s),              
      139 +    np.broadcast_to(np.arange(H // 2)[:, None], (H // 2, _n_pe
          +r_s)),                                                        
      140 +), "pack_single(): addr_row grouping is not uniform"          
      141  
      142 +                                                              
      143  def pack_single(frame: np.ndarray, lut: np.ndarray = _LUT) -> 
           bytes:
      144      """Pack an (H, w, 3) uint8 LINEAR strip into the SINGLE-CH
           AIN u8 byte stream.
      145  

● Update(~/Projects/rayglow/rayglow/render/hub75.py)
  ⎿  Added 7 lines, removed 9 lines
      157      w = frame.shape[1]
      158  
      159      g = lut[frame]
      160 -    pr, pg, pb = g[..., 0], g[..., 1], g[..., 2]              
      160 +    pr, pg, pb = g[..., 0][:, None, :], g[..., 1][:, None, :],
          + g[..., 2][:, None, :]                                        
      161 +    rb = (pr >> _planes_s[None]) & 1                          
      162 +    gb = (pg >> _planes_s[None]) & 1                          
      163 +    bb = (pb >> _planes_s[None]) & 1                          
      164 +    packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint8) << 
          +_shift_s[:, None, None]                                       
      165 +    packed = packed[:, :, ::-1]                 # col = w-1-x,
          + applied once                                                 
      166  
      162 -    fb3d = np.zeros((H // 2, B, w), dtype=np.uint8)           
      163 -    for y in range(H):                                        
      164 -        rb = (pr[y] >> _planes_s) & 1                         
      165 -        gb = (pg[y] >> _planes_s) & 1                         
      166 -        bb = (pb[y] >> _planes_s) & 1                         
      167 -        packed = ((bb << 2) | (gb << 1) | rb).astype(np.uint8)
          - << _shift_s[y]                                               
      168 -        fb3d[_addr_s[y], :, ::-1] |= packed     # col = w-1-x 
      169 -                                                              
      167 +    fb3d = np.bitwise_or.reduce(packed[_order_s].reshape(H // 
          +2, _n_per_s, B, w), axis=1)                                   
      168      return fb3d.reshape(-1).tobytes()           # u8, contiguo
           us
      169  
      170  

● Now the critical gate: verify byte-identity against the Rust golden frame. Let
  me run the verifier.

  Ran 1 shell command (ctrl+o to expand)

● All green — both packers are byte-identical to the firmware golden. Let me
  confirm the actual speed win with a quick microbenchmark (the whole point of
  this workstream):

  Ran 1 shell command (ctrl+o to expand)

● Workstream 1 done: 2.1× faster on the desktop, byte-identical (verify.py green
  + direct comparison). On the Pi's slower CPU the absolute ms saving will be
  larger.

  Now Workstream 2 (async PBO readback). Let me read the exact current structure
  of the GL bindings, the readback, and where Readback is constructed.

  Read 2 files (ctrl+o to expand)

● Now let me see how Readback is constructed and called in the pipeline, plus
  the warmup/main-loop integration in __main__.py.

  Searched for 1 pattern, read 1 file (ctrl+o to expand)

● PBO design is clear. Let me read the main loop's warmup usage and the
  arg/construction sites so I wire the flag and priming correctly.

  Read 1 file (ctrl+o to expand)

● All wired up in my head. Implementing Workstream 2 now — first the EGL
  bindings (imports, constants, PBO functions):

● Update(~/Projects/rayglow/rayglow/render/egl.py)
  ⎿  Added 1 line, removed 1 line
      11  """
      12  import ctypes
      13  from ctypes import (POINTER, byref, c_char, c_char_p, c_float, 
          c_int,
      14 -                    c_uint, c_void_p)                          
      14 +                    c_ssize_t, c_ubyte, c_uint, c_void_p)      
      15  
      16  _egl = ctypes.CDLL("libEGL.so.1", mode=ctypes.RTLD_GLOBAL)
      17  _gl = ctypes.CDLL("libGLESv2.so.2", mode=ctypes.RTLD_GLOBAL)

● Update(~/Projects/rayglow/rayglow/render/egl.py)
  ⎿  Added 5 lines
      67  GL_EXTENSIONS = 0x1F03
      68  GL_SHADING_LANGUAGE_VERSION = 0x8B8C
      69  
      70 +# Pixel buffer objects — async glReadPixels (GPU->CPU DMA witho
         +ut a CPU stall).                                               
      71 +GL_PIXEL_PACK_BUFFER = 0x88EB                                  
      72 +GL_STREAM_READ = 0x88E1                                        
      73 +GL_MAP_READ_BIT = 0x0001                                       
      74 +                                                               
      75  # -------------------------------------------------------------
          --------------
      76  # Function signatures
      77  # -------------------------------------------------------------
          --------------

● Update(~/Projects/rayglow/rayglow/render/egl.py)
  ⎿  Added 12 lines
      164  glGetError = _bind(_gl, "glGetError", c_uint, [])
      165  glGetString = _bind(_gl, "glGetString", c_char_p, [c_uint])
      166  
      167 +# GL — pixel buffer objects (async readback). glReadPixels int
          +o a bound                                                     
      168 +# GL_PIXEL_PACK_BUFFER returns immediately (the last arg becom
          +es a byte offset,                                             
      169 +# pass 0); glMapBufferRange then hands back the previous frame
          +'s bytes.                                                     
      170 +glGenBuffers = _bind(_gl, "glGenBuffers", None, [c_int, POINTE
          +R(c_uint)])                                                   
      171 +glBindBuffer = _bind(_gl, "glBindBuffer", None, [c_uint, c_uin
          +t])                                                           
      172 +glBufferData = _bind(_gl, "glBufferData", None,               
      173 +                     [c_uint, c_ssize_t, c_void_p, c_uint])   
      174 +glMapBufferRange = _bind(_gl, "glMapBufferRange", c_void_p,   
      175 +                         [c_uint, c_ssize_t, c_ssize_t, c_uint
          +])                                                            
      176 +glUnmapBuffer = _bind(_gl, "glUnmapBuffer", c_ubyte, [c_uint])
      177 +glDeleteBuffers = _bind(_gl, "glDeleteBuffers", None, [c_int, 
          +POINTER(c_uint)])                                             
      178  
      179 +                                                              
      180  # ------------------------------------------------------------
           ---------------
      181  # Helpers
      182  # ------------------------------------------------------------
           ---------------

● Now the Readback class with the 2-PBO ping-pong:

● Update(~/Projects/rayglow/rayglow/render/output.py)
  ⎿  Added 66 lines, removed 10 lines
       4  import numpy as np
       5  
       6  from . import egl
       7 -from .egl import GL_FRAMEBUFFER, GL_RGBA, GL_UNSIGNED_BYTE     
       7 +from .egl import (GL_FRAMEBUFFER, GL_MAP_READ_BIT, GL_PIXEL_PAC
         +K_BUFFER,                                                      
       8 +                  GL_RGBA, GL_STREAM_READ, GL_UNSIGNED_BYTE)   
       9  
      10  
      11  class Readback:
     ...
       19      frame at scale 4.  Instead we integer-sum the s*s box (fit
           s uint16: 64
       20      samples * 255 max) on the *contiguous* RGBA buffer and app
           ly gamma via a
       21      precomputed LUT indexed by that sum — exact and ~10x faste
           r.
       22 +                                                              
       23 +    `use_pbo` (default on) double-buffers the readback through
          + two pixel-pack                                               
       24 +    buffer objects: glReadPixels into PBO[cur] returns immedia
          +tely (async GPU                                               
       25 +    DMA, no CPU stall), and we map PBO[other] holding *last* f
          +rame's pixels.                                                
       26 +    Costs one frame of latency (fine for a visualizer); the fi
          +rst call returns                                              
       27 +    a black frame to prime the pipeline. Set use_pbo=False for
          + the exact,                                                   
       28 +    zero-latency synchronous path (used by dry-run so the GIF 
          +stays frame-exact).                                           
       29      """
       30  
       23 -    def __init__(self, width, height, scale, gamma):          
       31 +    def __init__(self, width, height, scale, gamma, use_pbo=Tr
          +ue):                                                          
       32          if not 1 <= scale <= 16:
       33              raise ValueError("scale must be in 1..16")
       34          self.w, self.h, self.scale = width, height, scale
       35 +        self.use_pbo = use_pbo                                
       36          self._buf = np.empty((height * scale, width * scale, 4
           ), np.uint8)
       37 +        self._nbytes = self._buf.nbytes                       
       38          # LUT over all possible box sums: sum in [0, s*s*255].
       39          sums = np.arange(scale * scale * 255 + 1, dtype=np.flo
           at32)
       40          x = sums / (scale * scale * 255.0)
       41          self._lut = (x ** gamma * 255.0 + 0.5).astype(np.uint8
           )
       42 +        if use_pbo:                                           
       43 +            ids = (ctypes.c_uint * 2)()                       
       44 +            egl.glGenBuffers(2, ids)                          
       45 +            self._pbo = [ids[0], ids[1]]                      
       46 +            for b in self._pbo:                               
       47 +                egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, b)     
       48 +                egl.glBufferData(GL_PIXEL_PACK_BUFFER, self._n
          +bytes, None,                                                  
       49 +                                 GL_STREAM_READ)              
       50 +            egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)         
       51 +            self._cur = 0        # PBO that THIS frame's readb
          +ack writes into                                               
       52 +            self._primed = False                              
       53 +            self._blank = np.zeros((height, width, 3), np.uint
          +8)                                                            
       54 +            egl.check_gl("PBO readback init")                 
       55  
       33 -    def read(self, fbo):                                      
       56 +    def _postprocess(self, rgba):                             
       57 +        """box-sum downsample -> gamma LUT -> v-flip -> drop a
          +lpha. Returns a                                               
       58 +        fresh contiguous (H,W,3) uint8 (copies out of any mapp
          +ed buffer)."""                                                
       59          s = self.scale
       35 -        egl.glBindFramebuffer(GL_FRAMEBUFFER, fbo)            
       36 -        egl.glReadPixels(0, 0, self.w * s, self.h * s,        
       37 -                         GL_RGBA, GL_UNSIGNED_BYTE,           
       38 -                         self._buf.ctypes.data_as(ctypes.c_voi
          -d_p))                                                         
       60          if s > 1:
       40 -            boxed = self._buf.reshape(self.h, s, self.w, s, 4)
          -.sum(                                                         
       61 +            boxed = rgba.reshape(self.h, s, self.w, s, 4).sum(
       62                  axis=(1, 3), dtype=np.uint16)
       63          else:
       43 -            boxed = self._buf                                 
       44 -        frame = self._lut[boxed[::-1, :, :3]]                 
       64 +            boxed = rgba                                      
       65 +        frame = self._lut[boxed[::-1, :, :3]]      # fancy-ind
          +ex -> fresh array                                             
       66          return np.ascontiguousarray(frame)
       67  
       68 +    def read(self, fbo):                                      
       69 +        s = self.scale                                        
       70 +        egl.glBindFramebuffer(GL_FRAMEBUFFER, fbo)            
       71 +        if not self.use_pbo:                                  
       72 +            egl.glReadPixels(0, 0, self.w * s, self.h * s,    
       73 +                             GL_RGBA, GL_UNSIGNED_BYTE,       
       74 +                             self._buf.ctypes.data_as(ctypes.c
          +_void_p))                                                     
       75 +            return self._postprocess(self._buf)               
       76  
       77 +        # Async path: kick this frame's readback into the curr
          +ent PBO (returns                                              
       78 +        # immediately — offset 0 because a PBO is bound), then
          + read the OTHER                                               
       79 +        # PBO, which has held last frame's pixels for a full f
          +rame (DMA done, so                                            
       80 +        # the map doesn't stall).                             
       81 +        egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbo[self.
          +_cur])                                                        
       82 +        egl.glReadPixels(0, 0, self.w * s, self.h * s,        
       83 +                         GL_RGBA, GL_UNSIGNED_BYTE, 0)        
       84 +        other = self._cur ^ 1                                 
       85 +        self._cur = other        # flip for next frame        
       86 +        if not self._primed:                                  
       87 +            self._primed = True                               
       88 +            egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)         
       89 +            return self._blank                                
       90 +        egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbo[other
          +])                                                            
       91 +        ptr = egl.glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, se
          +lf._nbytes,                                                   
       92 +                                   GL_MAP_READ_BIT)           
       93 +        if not ptr:                                           
       94 +            raise egl.GLError("glMapBufferRange returned NULL"
          +)                                                             
       95 +        mapped = np.frombuffer(                               
       96 +            (ctypes.c_ubyte * self._nbytes).from_address(ptr),
          + dtype=np.uint8                                               
       97 +        ).reshape(self.h * s, self.w * s, 4)                  
       98 +        frame = self._postprocess(mapped)          # copies be
          +fore unmap                                                    
       99 +        egl.glUnmapBuffer(GL_PIXEL_PACK_BUFFER)               
      100 +        egl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)             
      101 +        return frame                                          
      102 +                                                              
      103 +                                                              
      104  def save_gif(frames, path, fps, upscale=3):
      105      """Write dry-run frames as an animated GIF, nearest-upscal
           ed so a 256x32
      106      strip is actually eyeballable."""

● Now thread the flag through ShaderToy and add the CLI option (gating dry-run
  to the sync path for frame-exact GIFs):

● Update(~/Projects/rayglow/rayglow/render/pipeline.py)
  ⎿  Added 3 lines, removed 2 lines
      33      panel-ready (H, W, 3) uint8 numpy array.
      34      """
      35  
      36 -    def __init__(self, width, height, scale=4, gamma=1.2, base_
         -dir=None):                                                     
      36 +    def __init__(self, width, height, scale=4, gamma=1.2, base_
         +dir=None,                                                      
      37 +                 use_pbo=True):                                
      38          self.width, self.height, self.scale = width, height, sc
          ale
      39          self.base_dir = base_dir          # directive image pat
          hs resolve here
      40          # Unused samplers bind to this 1x1 black texture so the
          y're valid.
      41          self.dummy_tex = passes.make_texture(1, 1, bytes(4))
      42          self.passes = {"image": passes.Pass("image", width * sc
          ale,
      43                                              height * scale, sel
          f.dummy_tex)}
      43 -        self.readback = Readback(width, height, scale, gamma)  
      44 +        self.readback = Readback(width, height, scale, gamma, u
         +se_pbo=use_pbo)                                                
      45          self.audio_channels = []          # live list; AudioFee
          d iterates it
      46          self.buffer_format = passes.pick_buffer_format()
      47          self._cli_specs = {}              # image-pass override
          s {index: spec}
● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 3 lines
      275      ap.add_argument("--no-listen", action="store_true",
      276                      help="audio channel: never bind the UDP so
           cket, "
      277                           "synth fallback only")
      278 +    ap.add_argument("--no-pbo", action="store_true",          
      279 +                    help="disable async PBO readback (use the 
          +synchronous "                                                 
      280 +                         "glReadPixels path; no one-frame late
          +ncy)")                                                        
      281      args = ap.parse_args()
      282  
      283      # Geometry defaults to the full two-chain display (256x64)
           . The render

● Now wire use_pbo into the ShaderToy construction, gating dry-run to the
  synchronous path:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 4 lines, removed 1 line
      295          sys.exit(1)
      296      print(f"GPU: {ctx.info()}")
      297  
      298 +    # PBO async readback helps only the live streaming loop; d
          +ry-run uses the                                               
      299 +    # synchronous path so the GIF stays frame-exact (no one-fr
          +ame shift/drop).                                              
      300 +    use_pbo = (args.dry_run is None) and not args.no_pbo      
      301      toy = ShaderToy(args.width, args.height, scale=args.scale,
      299 -                    gamma=args.gamma,                         
      302 +                    gamma=args.gamma, use_pbo=use_pbo,        
      303                      base_dir=os.path.dirname(os.path.abspath(a
           rgs.shader)))
      304      for i in range(4):
      305          spec = getattr(args, f"channel{i}")

● Now let me smoke-test. First find a shader and run a dry-run (exercises the
  refactored sync path), then validate the PBO path produces identical output
  shifted by one frame.

  Ran 2 shell commands (ctrl+o to expand)

● Sync path works on the desktop EGL (NVIDIA). Now the key validation — that the
  PBO path is bit-identical to sync, just shifted one frame:

  Ran 1 shell command (ctrl+o to expand)

● Both Pi-side workstreams are done and validated on the desktop EGL. Here's
  where things stand.

  Done — Workstream 1: vectorized pack()

  - Replaced the per-row Python loop in both pack() and pack_single()
  (rayglow/render/hub75.py) with a grouped bitwise_or.reduce, guarded by a
  module-load assertion on the addr_row grouping invariant.
  - tools/verify.py → ALL GREEN (byte-identical to the firmware golden, both
  two-chain and single-chain).
  - 2.1× faster on the desktop (1.23 ms → 0.58 ms/frame); the absolute saving is
  larger on the Pi's slower CPU.

  Done — Workstream 2: async PBO readback

  - Added the PBO entry points to egl.py
  (glGenBuffers/BindBuffer/BufferData/MapBufferRange/UnmapBuffer/DeleteBuffers +
  constants).
  - Readback (output.py) now ping-pongs two pixel-pack buffers: glReadPixels
  returns immediately, and we map last frame's PBO (one-frame latency, primed
  with a blank first frame). Refactored the shared box-sum/LUT/flip into
  _postprocess.
  - Threaded use_pbo through ShaderToy; added --no-pbo; dry-run is forced to the
  sync path so GIFs stay frame-exact.
  - Validated byte-identical to the sync path, shifted exactly one frame (frames
  1-9 of the PBO run matched frames 0-8 of sync; frame 0 was the blank prime).

  Both changes are Pi-side only — no firmware, no wire-format change — so this
  is the "land on main" stage. The PBO win only shows on the Pi (the desktop is
  too fast to stall), so that's where to measure it.

  Your git question, concretely

  Anchor the proven build, then commit these two low-risk wins to main:
  git tag -a v0.1.0 -m "proven 24MHz SPI link"   # your always-safe return point
  # then, after you've tested on the Pi:
  git add rayglow/render/hub75.py rayglow/render/egl.py rayglow/render/output.py
  \
          rayglow/render/pipeline.py rayglow/render/__main__.py
  git commit -m "Vectorize bit-plane pack + async PBO readback"
  (I haven't run any git commands — that's yours to drive. Note there were
  pre-existing working-tree modifications from before this session; you'll want
  to review those separately so they don't get swept into this commit.)

  To test on the Pi: run your normal stream and watch the 5-second telemetry
  line — pack should drop, and render (which includes readback) should drop once
  the GPU stall is hidden. --no-pbo toggles the readback change for an A/B.

  Next: Workstream 3 (parallel PIO transport)

  This is the feature-branch stage, and per the plan it's gated behind "1+2
  proven on the panel" — because it needs hardware I can't reach from here: a C
  shim built against libpio on the Pi, new firmware flashed to the RP2350,
  rewiring, and a logic-analyzer bring-up to nail the lane↔bit ordering.

  I can scaffold it now on a feat/pio-parallel-link branch — write
  phase6_parallel.rs (8-lane RX), the pio_shim.c + PioOut, and the --transport
  factory — so it's ready for you to build/flash/wire and bench. But it'll be
  unvalidated code until you're at the bench. Want me to start that scaffolding 
  now, or pause here so you can land + Pi-test the two wins first?

✻ Worked for 18m 30s

※ recap: Goal: get rayglow past its SPI bottleneck. Done and validated: vectorized packing and async PBO readback (the two Pi-side wins), ready to commit to main. Next: decide whether I scaffold Workstream 3, the parallel PIO transport, on a branch.

❯ Long traceback from the rpi. Possibly to do with how I am now mounting the    
  folder. The duplicate folder was (rpi)~/rayglow which I removed (backed-up    
  technically) and replaced with an empty (rpi)~/rayglow and mounted            
  (will-desktop)~/Projects/rayglow to that folder so it would be the exact same 
  folder structure, just symlinked so I could have a single source of truth     
  for the code. The program ran in that configuration prevously, or I assume it 
  was without being cached or something, e.g. I stopped and restarted the       
  command after symlinking and it ran. All that to say, I'm not sure why lgpio  
  would throw and error. The other two deps may just need to be added:          
                                                                                
  will@raspberrypi ~/rayglow (main)> sudo ~/venv/bin/python -m rayglow.render   
  ~/rayglow/rayglow/render/presets/will-helix.glsl --spi-hz 40000000            
  GPU: V3D 7.1.7.0 | OpenGL ES 3.1 Mesa 25.0.7-2+rpt4 | GLSL OpenGL ES GLSL ES  
  3.10                                                                          
  image iChannel0 <- milk (milk)                                                
  audio: listening on UDP                                                       
  2026-06-17 14:42:27 xCreatePipe: Can't set permissions (436) for              
  /home/will/rayglow/.lgd-nfy0, No such file or directory                       
  /home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py:300:         
  PinFactoryFallback: Falling back from lgpio: [Errno 2] No such file or        
  directory: '.lgd-nfy-3'                                                       
    warnings.warn(                                                              
  /home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py:300:         
  PinFactoryFallback: Falling back from rpigpio: No module named 'RPi'          
    warnings.warn(                                                              
  /home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py:300:         
  PinFactoryFallback: Falling back from pigpio: No module named 'pigpio'        
    warnings.warn(                                                              
  /home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py:300:         
  PinFactoryFallback: Falling back from native: unable to determine gpio base   
    warnings.warn(                                                              
  Traceback (most recent call last):                                            
    File "<frozen runpy>", line 198, in _run_module_as_main                     
    File "<frozen runpy>", line 88, in _run_code                                
    File "/home/will/rayglow/rayglow/render/__main__.py", line 337, in <module> 
      main()                                                                    
      ~~~~^^                                                                    
    File "/home/will/rayglow/rayglow/render/__main__.py", line 333, in main     
      run_spi(toy, watchers, feed, args)                                        
      ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^                                        
    File "/home/will/rayglow/rayglow/render/__main__.py", line 169, in run_spi  
      out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)                      
    File "/home/will/rayglow/rayglow/render/spi_out.py", line 28, in __init__   
      self.ready = DigitalInputDevice(ready_bcm, pull_up=False)                 
                   ~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^                 
    File "/home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py",    
  line 108, in __call__                                                         
      self = super().__call__(*args, **kwargs)                                  
    File                                                                        
  "/home/will/venv/lib/python3.13/site-packages/gpiozero/input_devices.py",     
  line 162, in __init__                                                         
      super().__init__(                                                         
      ~~~~~~~~~~~~~~~~^                                                         
          pin, pull_up=pull_up, active_state=active_state,                      
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^                      
          pin_factory=pin_factory)                                              
          ^^^^^^^^^^^^^^^^^^^^^^^^                                              
    File "/home/will/venv/lib/python3.13/site-packages/gpiozero/mixins.py",     
  line 243, in __init__                                                         
      super().__init__(*args, **kwargs)                                         
      ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^                                         
    File                                                                        
  "/home/will/venv/lib/python3.13/site-packages/gpiozero/input_devices.py",     
  line 79, in __init__                                                          
      super().__init__(pin, pin_factory=pin_factory)                            
      ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^                            
    File "/home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py",    
  line 544, in __init__                                                         
      super().__init__(pin_factory=pin_factory)                                 
      ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^                                 
    File "/home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py",    
  line 245, in __init__                                                         
      Device.ensure_pin_factory()                                               
      ~~~~~~~~~~~~~~~~~~~~~~~~~^^                                               
    File "/home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py",    
  line 270, in ensure_pin_factory                                               
      Device.pin_factory = Device._default_pin_factory()                        
                           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^                        
    File "/home/will/venv/lib/python3.13/site-packages/gpiozero/devices.py",    
  line 302, in _default_pin_factory                                             
      raise BadPinFactory('Unable to load any default pin factory!')            
  gpiozero.exc.BadPinFactory: Unable to load any default pin factory!           

● That traceback looks scary but it's a one-line fix, and it's not a missing
  dependency — don't install pigpio/RPi.GPIO, they won't help (and RPi.GPIO
  doesn't even support the Pi 5's RP1 chip). The real culprit is exactly what
  you suspected: the mount. Let me explain the mechanism, because it's a good
  one to know.

  What's actually failing

  Look at the first error line, before all the fallback noise:

  xCreatePipe: Can't set permissions (436) for /home/will/rayglow/.lgd-nfy0, No
  such file or directory

  lgpio (the correct, Pi-5-native GPIO backend) uses a named pipe (FIFO) for its
  event-notification IPC. It creates that FIFO — .lgd-nfy* — in the current 
  working directory, then chmods it to 0664 (436 decimal = 0o664 — that's the
  "permissions (436)" in the message). You launched from ~/rayglow, so it tried
  to create the FIFO at /home/will/rayglow/.lgd-nfy0.

  But /home/will/rayglow is now your sshfs/NFS mount of the desktop folder.
  Network filesystems can't host FIFOs — mkfifo/mknod and the chmod semantics
  lgpio needs don't work over sshfs/NFS. So the FIFO creation fails → lgpio init
  throws → gpiozero falls through every other factory (rpigpio, pigpio, native
  — all expected to fail on a Pi 5) → BadPinFactory.

  That's why it broke when you swapped the real directory for the mount, and 
  worked before. Nothing to do with your code or caching — lgpio just needs a
  local filesystem to drop its scratch FIFO, and your CWD stopped being one.
  Your ~/rayglow is the mount; /home/will itself is still local.

  The fix: launch from a local working directory

  python -m rayglow.render resolves through the editable install regardless of
  CWD, so just run it from anywhere local — /tmp is the conventional scratch
  home for exactly this kind of file:

  cd /tmp
  sudo ~/venv/bin/python -m rayglow.render
  ~/rayglow/rayglow/render/presets/will-helix.glsl --spi-hz 40000000

  The ~/rayglow/... shader path still points into the mount (root can read it —
  that's how the GLSL hot-reload already works), and lgpio now drops its
  .lgd-nfy* FIFO in /tmp (local ext4, FIFOs fine). sudo inherits your CWD, so
  the cd /tmp is what matters, not where the venv lives.

  If you want it permanent without thinking about it, a tiny fish function
  works:
  function rayglow --wraps 'rayglow render'
      cd /tmp; and sudo ~/venv/bin/python -m rayglow.render $argv
  end

  This will bite the Workstream 3 PioOut too — it uses the same gpiozero/lgpio
  factory for the READY line (and CS) — so the "launch from a local CWD" rule
  carries forward. Let me note it for next time.

  Wrote 1 memory (ctrl+o to expand)

● Noted for future sessions. Give that cd /tmp launch a try — the renderer (with
  the new vectorized pack + PBO readback) should come up clean, and you'll see
  whether the two Pi-side wins move the pack/render numbers in the telemetry
  line. If lgpio still complains from /tmp, send me the new output and we'll dig
  further, but I'd be surprised — the FIFO-on-a-network-mount story fits every
  line of that traceback.

✻ Brewed for 2m 0s

※ recap: Goal: get RayGLow's LED-wall pipeline past its SPI bottleneck toward higher FPS. Vectorized pack and async PBO readback are done and validated; next, run the renderer on the Pi from /tmp to confirm the two Pi-side wins, then start the parallel-PIO transport.

❯ I'm showing the full command for the first two and then the median test       
  results for various shaders:                                                  
                                                                                
  will@raspberrypi /tmp> cd /tmp                                                
                         sudo ~/venv/bin/python -m rayglow.render               
  ~/rayglow/rayglow/render/presets/will-circles.glsl --spi-hz 40000000          
  GPU: V3D 7.1.7.0 | OpenGL ES 3.1 Mesa 25.0.7-2+rpt4 | GLSL OpenGL ES GLSL ES  
  3.10                                                                          
  image iChannel0 <- milk (milk)                                                
  audio: listening on UDP                                                       
  spi_out: SPI0.0 @ 40.00 MHz mode 0, READY=GPIO25                              
    55.4 fps | render   9.4ms  pack  0.6ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    55.4 fps | render   9.4ms  pack  0.6ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    55.4 fps | render   9.4ms  pack  0.6ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    55.3 fps | render   9.4ms  pack  0.6ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
                                                                                
  will@raspberrypi /tmp> cd /tmp                                                
                         sudo ~/venv/bin/python -m rayglow.render               
  ~/rayglow/rayglow/render/presets/will-circles.glsl --spi-hz 40000000 --no-pbo 
  GPU: V3D 7.1.7.0 | OpenGL ES 3.1 Mesa 25.0.7-2+rpt4 | GLSL OpenGL ES GLSL ES  
  3.10                                                                          
  image iChannel0 <- milk (milk)                                                
  audio: listening on UDP                                                       
  spi_out: SPI0.0 @ 40.00 MHz mode 0, READY=GPIO25                              
    60.0 fps | render   6.2ms  pack  0.7ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    59.8 fps | render   7.5ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    59.8 fps | render   7.5ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    59.8 fps | render   7.5ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
                                                                                
  Others:                                                                       
  golfing-with-gemma-1.glsl                                                     
    55.0 fps | render   9.5ms  pack  0.6ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    59.8 fps | render   7.3ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB) --no-pbo                                                         
                                                                                
  golfing-with-gemma-6.glsl                                                     
    33.6 fps | render  20.9ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    40.0 fps | render  16.0ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB) --no-pbo                                                         
                                                                                
  will-blob.glsl                                                                
    47.6 fps | render  12.4ms  pack  0.6ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB)                                                                  
    53.5 fps | render   9.9ms  pack  0.8ms  send   7.9ms (SPI floor  6.6ms @    
  40MHz, 32KB) --no-pbo                                                         

● This is a genuinely valuable result — and it says the PBO optimization
  backfired on the real hardware. Look at the pattern: every shader is slower
  with PBO (the default) than with --no-pbo:

  ┌──────────────┬────────────┬─────────────────┬──────────┐
  │    Shader    │ PBO render │ --no-pbo render │    Δ     │
  ├──────────────┼────────────┼─────────────────┼──────────┤
  │ will-circles │ 9.4 ms     │ 6.2–7.5 ms      │ +~2.5 ms │
  ├──────────────┼────────────┼─────────────────┼──────────┤
  │ golfing-1    │ 9.5 ms     │ 7.3 ms          │ +2.2 ms  │
  ├──────────────┼────────────┼─────────────────┼──────────┤
  │ golfing-6    │ 20.9 ms    │ 16.0 ms         │ +4.9 ms  │
  ├──────────────┼────────────┼─────────────────┼──────────┤
  │ will-blob    │ 12.4 ms    │ 9.9 ms          │ +2.5 ms  │
  └──────────────┴────────────┴─────────────────┴──────────┘

  PBO consistently adds 2.5–5 ms. Meanwhile vectorized pack is a clean win —
  pack is 0.6–0.8 ms, basically free now. So Workstream 1 stays; Workstream 2
  needs to be reverted to off. Let me do that and explain why it lost, because
  the why is the interesting part.

  Why PBO async readback is a desktop optimization that backfires on V3D

  The PBO trick rests on two assumptions, both false on the Pi:

  1. "glReadPixels is a hard stall over a slow bus." True on a discrete GPU: the
  framebuffer lives in VRAM across PCIe, and the sync readback blocks the CPU
  while DMA crawls over the bus — worth hiding. But the Pi's V3D is a tiled 
  renderer with unified memory — the framebuffer is already in the same physical
  RAM the CPU uses. There's no PCIe crossing to hide. glReadPixels here is
  comparatively cheap (a flush + a same-RAM copy into your cached numpy buffer).
  2. "You have other CPU work to overlap with the async DMA." The loop is serial
  — render → readback → pack → send. The PBO defers the readback by a frame,
  but you immediately glMapBufferRange and process it, with nothing to overlap.
  Worse: the box-sum downsample now reads the mapped GPU buffer directly, and
  that memory is typically uncached / write-combined — streaming 512 KB of
  supersampled pixels through it per frame (scale 4 → 1024×128×4) is far slower
  on the CPU than the sync path, which reads from an ordinary cached numpy array
  the driver filled efficiently. That uncached-read penalty is your +2.5–5 ms.

  So you traded a stall that barely exists for a slow-memory read that
  definitely does. This is exactly the kind of thing that only the hardware can
  tell you — good call running the A/B. Reverting the default now:

● Update(~/Projects/rayglow/rayglow/render/output.py)
  ⎿  Added 10 lines, removed 7 lines
      20      samples * 255 max) on the *contiguous* RGBA buffer and appl
          y gamma via a
      21      precomputed LUT indexed by that sum — exact and ~10x faster
          .
      22  
      23 -    `use_pbo` (default on) double-buffers the readback through 
         -two pixel-pack                                                 
      24 -    buffer objects: glReadPixels into PBO[cur] returns immediat
         -ely (async GPU                                                 
      25 -    DMA, no CPU stall), and we map PBO[other] holding *last* fr
         -ame's pixels.                                                  
      26 -    Costs one frame of latency (fine for a visualizer); the fir
         -st call returns                                                
      27 -    a black frame to prime the pipeline. Set use_pbo=False for 
         -the exact,                                                     
      28 -    zero-latency synchronous path (used by dry-run so the GIF s
         -tays frame-exact).                                             
      23 +    `use_pbo` double-buffers the readback through two pixel-pac
         +k buffer objects:                                              
      24 +    glReadPixels into PBO[cur] returns immediately, then we map
         + PBO[other]                                                    
      25 +    holding *last* frame's pixels (one frame of latency; first 
         +call returns a                                                 
      26 +    black prime frame). This is a DISCRETE-GPU optimization and
         + is **off by                                                   
      27 +    default** because it measured ~2.5-5ms SLOWER on the Pi's V
         +3D: unified memory                                             
      28 +    means glReadPixels isn't a bus stall to hide, and the box-s
         +um then streams                                                
      29 +    the supersampled frame out of the *uncached* mapped buffer 
         +(vs the cached                                                 
      30 +    numpy buffer the sync path fills). Kept behind `--pbo` for 
         +provenance / other                                             
      31 +    GPUs. The synchronous path is the default and the faster on
         +e here.                                                        
      32      """
      33  
      31 -    def __init__(self, width, height, scale, gamma, use_pbo=Tru
         -e):                                                            
      34 +    def __init__(self, width, height, scale, gamma, use_pbo=Fal
         +se):                                                           
      35          if not 1 <= scale <= 16:
      36              raise ValueError("scale must be in 1..16")
      37          self.w, self.h, self.scale = width, height, scale

● Update(~/Projects/rayglow/rayglow/render/pipeline.py)
  ⎿  Added 1 line, removed 1 line
      34      """
      35  
      36      def __init__(self, width, height, scale=4, gamma=1.2, base_
          dir=None,
      37 -                 use_pbo=True):                                
      37 +                 use_pbo=False):                               
      38          self.width, self.height, self.scale = width, height, sc
          ale
      39          self.base_dir = base_dir          # directive image pat
          hs resolve here
      40          # Unused samplers bind to this 1x1 black texture so the
          y're valid.

● Now flip the CLI flag from opt-out to opt-in:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 3 lines, removed 3 lines
      275      ap.add_argument("--no-listen", action="store_true",
      276                      help="audio channel: never bind the UDP so
           cket, "
      277                           "synth fallback only")
      278 -    ap.add_argument("--no-pbo", action="store_true",          
      279 -                    help="disable async PBO readback (use the 
          -synchronous "                                                 
      280 -                         "glReadPixels path; no one-frame late
          -ncy)")                                                        
      278 +    ap.add_argument("--pbo", action="store_true",             
      279 +                    help="async PBO readback (experimental; me
          +asured SLOWER on "                                            
      280 +                         "the Pi's V3D — default is the synchr
          +onous path)")                                                 
      281      args = ap.parse_args()
      282  
      283      # Geometry defaults to the full two-chain display (256x64)
           . The render

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 4 lines, removed 3 lines
      295          sys.exit(1)
      296      print(f"GPU: {ctx.info()}")
      297  
      298 -    # PBO async readback helps only the live streaming loop; d
          -ry-run uses the                                               
      299 -    # synchronous path so the GIF stays frame-exact (no one-fr
          -ame shift/drop).                                              
      300 -    use_pbo = (args.dry_run is None) and not args.no_pbo      
      298 +    # PBO async readback is experimental and off by default (s
          +lower on V3D, see                                             
      299 +    # output.Readback); only ever for the live loop, never dry
          +-run (the one-frame                                           
      300 +    # shift/drop would skew the GIF).                         
      301 +    use_pbo = (args.dry_run is None) and args.pbo             
      302      toy = ShaderToy(args.width, args.height, scale=args.scale,
      303                      gamma=args.gamma, use_pbo=use_pbo,
      304                      base_dir=os.path.dirname(os.path.abspath(a
           rgs.shader)))

● Let me smoke-test that the flag rename didn't break the CLI:

  Ran 1 shell command (ctrl+o to expand)

● Reverted and verified — sync readback is the default now, --pbo is opt-in
  (with the V3D finding documented in output.py so nobody re-attempts it
  blindly). The dry-run still renders clean and the flag is wired.

  Now let me read your numbers, because they tell you exactly where to push
  next:

  Two of your shaders are hitting the --fps 60 cap, not a bottleneck.
  will-circles and golfing-1 at --no-pbo sit at 59.8–60.0 fps: render 7 + pack
  0.8 + send 7.9 ≈ 15.5 ms, under the 16.7 ms budget. To see their true ceiling,
  run --fps 240.

  The heavy shaders are render+send bound, serially. golfing-6 = 16 + 0.8 + 7.9
  ≈ 25 ms → 40 fps. will-blob ≈ 18.6 ms → 53 fps. And send is 7.9 ms everywhere
  (6.6 ms SPI floor + ~1.3 ms READY/overhead) — that's your link, exactly the
  cost Workstream 3 erases.

  The correct version of what PBO was trying to do

  PBO tried to hide latency in the wrong place. The real latency to hide is that
  7.9 ms send is pure serial dead time — the CPU is just clocking bytes and
  waiting on READY while the GPU sits idle. Overlap it: render frame N while 
  send(N-1) runs on a background thread. The cadence goes from render + pack + 
  send to max(render+pack, send):

  - will-blob: 18.6 ms → max(10.7, 7.9) = 10.7 ms ≈ 93 fps (from 53)
  - golfing-6: 24.7 ms → max(16.8, 7.9) = 16.8 ms ≈ 60 fps (from 40)

  This works today, over the existing SPI link, no hardware. And it's genuinely
  effective in Python because the GIL is released during the spidev write, the
  READY wait, glReadPixels, and numpy — so the send thread really does run
  concurrently with the next render. It just needs double-buffering the payload
  so you don't overwrite bytes mid-transfer. This is the "right PBO": pipeline
  at the loop level, where there's actual overlappable work.

  A second, complementary render-side win: downsample on the GPU. Your render
  number includes reading back a 16×-oversized buffer (scale 4) and box-summing
  it on the CPU. A GPU downsample pass would shrink the readback 16× and delete
  the CPU box-sum — biggest help on the light shaders where readback, not
  shading, dominates render.

  Where to point next — your call

  - (A) Pipeline render ∥ send — software-only, works now, ~1.5–1.75× on the
  heavy shaders. The highest-leverage immediate win and the proper completion of
  the "Pi-side wins" stage. My recommendation.
  - (B) GPU-side downsample — cuts the readback/box-sum portion of render;
  medium effort (a resolve pass in the pipeline).
  - (C) Workstream 3 (parallel PIO) — eliminates the 7.9 ms send outright (→ <1
  ms); needs the firmware branch + C shim + bench bring-up.

  (A) and (C) attack the same send cost — A hides it, C removes it — so A buys
  you most of C's benefit with zero hardware, which is why I'd do A next and
  keep C for when you're at the bench. Want me to implement the render∥send
  pipeline?
✻ Baked for 3m 22s

❯ (A) first, the render / send pipeline. I will include a few more intensive    
  shaders next time to get a better range of samples as well.                   

● On it. Let me re-read the current run_spi so I restructure it precisely.

  Read 1 file (ctrl+o to expand)

● Let me check threading import availability and what pin_to_core does (it
  matters — the send thread must not get pinned to the render core, or there's
  no real overlap):

  Searched for 1 pattern (ctrl+o to expand)

● pin_to_core pins only the calling thread (sched_setaffinity(0, ...)). So I'll
  create the send worker before pinning main — the worker inherits the full-core
  affinity and floats onto an idle core, while main stays pinned to the render
  core. That's what makes the overlap real instead of two threads fighting over
  core 0.

  Implementing now — first the import and the _SendPipe helper:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 1 line
      36  import os
      37  import re
      38  import sys
      39 +import threading                                               
      40  import time
      41  
      42  import numpy as np

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 59 lines
      146      sys.exit(0 if ok else 1)
      147  
      148  
      149 +class _SendPipe:                                              
      150 +    """Background SPI sender that overlaps frame N's transfer 
          +with frame N+1's                                              
      151 +    render. `out.send()` blocks for the SPI floor + READY wait
          + (~8 ms); running                                             
      152 +    it on a worker thread lets the main thread render+pack the
          + next frame                                                   
      153 +    meanwhile, so the loop cadence becomes max(render+pack, se
          +nd) instead of                                                
      154 +    their sum. Depth-1 (one frame in flight) keeps the added l
          +atency to a single                                            
      155 +    frame. Only the worker touches `out`, so the SPI/GPIO obje
          +cts stay                                                      
      156 +    single-threaded. The GIL is released during the spidev wri
          +te and READY wait,                                            
      157 +    so the overlap is real.                                   
      158 +    """                                                       
      159 +                                                              
      160 +    def __init__(self, out):                                  
      161 +        self._out = out                                       
      162 +        self._payload = None                                  
      163 +        self._work = threading.Event()    # main -> worker: a 
          +payload is ready                                              
      164 +        self._idle = threading.Event()    # worker -> main: pr
          +evious send done                                              
      165 +        self._idle.set()                  # start idle        
      166 +        self._stop = False                                    
      167 +        self._exc = None                                      
      168 +        self.acc_send = 0.0               # worker: cumulative
          + transfer seconds                                             
      169 +        self._thread = threading.Thread(target=self._run, daem
          +on=True)                                                      
      170 +        self._thread.start()                                  
      171 +                                                              
      172 +    def _run(self):                                           
      173 +        while True:                                           
      174 +            self._work.wait()                                 
      175 +            self._work.clear()                                
      176 +            if self._stop:                                    
      177 +                return                                        
      178 +            try:                                              
      179 +                t = time.perf_counter()                       
      180 +                self._out.send(self._payload)                 
      181 +                self.acc_send += time.perf_counter() - t      
      182 +            except BaseException as e:    # surface to main on
          + next submit()                                                
      183 +                self._exc = e                                 
      184 +            self._idle.set()                                  
      185 +                                                              
      186 +    def submit(self, payload):                                
      187 +        """Block until the previous send finishes (the residua
          +l send-bound                                                  
      188 +        stall), hand off `payload`, and return that wait time 
          +in seconds. The                                               
      189 +        worker transfers it while the caller renders the next 
          +frame."""                                                     
      190 +        t = time.perf_counter()                               
      191 +        self._idle.wait()                                     
      192 +        wait = time.perf_counter() - t                        
      193 +        if self._exc is not None:                             
      194 +            raise self._exc                                   
      195 +        self._idle.clear()                                    
      196 +        self._payload = payload                               
      197 +        self._work.set()                                      
      198 +        return wait                                           
      199 +                                                              
      200 +    def close(self):                                          
      201 +        self._idle.wait()                                     
      202 +        self._stop = True                                     
      203 +        self._work.set()                                      
      204 +        self._thread.join(timeout=2.0)                        
      205 +        self._out.close()                                     
      206 +                                                              
      207 +                                                              
      208  def run_spi(toy, watchers, feed, args):
      209      """Render + pack + ship frames to the rp2350b over SPI (th
           e only output).
      210  

● Now restructure the loop body to use the pipe (worker created before pinning
  main) with updated telemetry:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 24 lines, removed 14 lines
      227          pack(warm)
      228  
      229      out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)
      230 +    # Build the send worker BEFORE pinning, so it inherits the
          + full-core affinity                                           
      231 +    # and floats onto an idle core; pin_to_core then pins only
          + the render thread.                                           
      232 +    pipe = _SendPipe(out)                                     
      233      pin_to_core(config.RENDER_CORE)
      234  
      235      frame_interval = 1.0 / args.fps
     ...
      237      last = t0
      238      fps_frames, fps_t = 0, t0
      239      frame = 0
      237 -    # Per-stage time accumulators (seconds) for the bottleneck
          - breakdown below.                                             
      238 -    acc_render = acc_pack = acc_send = 0.0                    
      240 +    # Per-stage accumulators. render+pack run on this thread; 
          +the SPI transfer                                              
      241 +    # runs on the worker (pipe.acc_send). `acc_wait` is how lo
          +ng this thread                                                
      242 +    # blocks waiting for the previous transfer — the residual 
          +send-bound stall                                              
      243 +    # AFTER overlap (≈0 => the link is fully hidden behind ren
          +der).                                                         
      244 +    acc_render = acc_pack = acc_wait = 0.0                    
      245 +    pipe.acc_send = 0.0                                       
      246      last_bytes = 0
      247      try:
      248          while True:
     ...
      268              tb = time.perf_counter()
      269              payload = pack_single(buf) if config.SPI_SINGLE_CH
           AIN else pack(buf)
      270              tc = time.perf_counter()
      264 -            out.send(payload)             # blocks on READY (f
          -irmware/refresh) + clocks bytes                               
      265 -            td = time.perf_counter()                          
      271 +            # Hand the frame to the worker; it transfers while
          + we render the next.                                          
      272 +            # submit() blocks only if the previous transfer ha
          +sn't finished.                                                
      273 +            wait = pipe.submit(payload)   # fresh immutable by
          +tes => no aliasing                                            
      274              acc_render += tb - ta         # GLSL render + read
           back + flips + fold
      275              acc_pack += tc - tb           # bit-plane packing
      268 -            acc_send += td - tc           # READY wait + SPI b
          -yte transfer                                                  
      276 +            acc_wait += wait              # stall on the previ
          +ous send (overlap residue)                                    
      277              last_bytes = len(payload)
      278              last = now
      279              frame += 1
     ...
      281              fps_frames += 1
      282              if now - fps_t >= 5.0:
      283                  n = fps_frames
      276 -                # Theoretical SPI-transfer floor for this fram
          -e size+clock: if                                              
      277 -                # `send` ms hugs this, the LINK is the clamp; 
          -if `send` >> floor,                                           
      278 -                # the firmware/refresh (READY wait) is; if `re
          -nder` dominates, the                                          
      279 -                # shader is. `pack` is usually negligible.    
      284 +                # send = the worker's actual transfer time (li
          +nk cost); wait =                                              
      285 +                # how much it leaked into the critical path. I
          +f wait hugs 0 the                                             
      286 +                # link is fully hidden and `render` is the cla
          +mp; if wait ~ send,                                           
      287 +                # the link still paces. SPI floor is the theor
          +etical transfer min.                                          
      288                  spi_floor_ms = last_bytes * 8 / args.spi_hz * 
           1e3
      289 +                send_ms = pipe.acc_send / n * 1e3             
      290                  print(f"{n / (now - fps_t):6.1f} fps | "
      291                        f"render {acc_render / n * 1e3:5.1f}ms  
           "
      292                        f"pack {acc_pack / n * 1e3:4.1f}ms  "
      284 -                      f"send {acc_send / n * 1e3:5.1f}ms "    
      293 +                      f"send {send_ms:5.1f}ms  wait {acc_wait 
          +/ n * 1e3:5.1f}ms "                                           
      294                        f"(SPI floor {spi_floor_ms:4.1f}ms @ {ar
           gs.spi_hz/1e6:.0f}MHz, "
      295                        f"{last_bytes//1024}KB)")
      296                  fps_frames, fps_t = 0, now
      288 -                acc_render = acc_pack = acc_send = 0.0        
      289 -            # READY paces to the rp2350b's commit; also cap to
          - --fps so we don't                                            
      290 -            # render frames nobody asked for.                 
      297 +                acc_render = acc_pack = acc_wait = 0.0        
      298 +                pipe.acc_send = 0.0                           
      299 +            # Cap to --fps so we don't render frames nobody as
          +ked for (the worker                                           
      300 +            # + READY handshake otherwise self-pace to the rp2
          +350b).                                                        
      301              sleep = frame_interval - (time.perf_counter() - no
           w)
      302              if sleep > 0:
      303                  time.sleep(sleep)
      304      except KeyboardInterrupt:
      305          pass
      306      finally:
      297 -        out.close()                                           
      307 +        pipe.close()                                          
      308  
      309  
      310  def main():

● Now let me test the pipeline logic with a simulated SPI sink (no hardware
  needed) — verify it actually overlaps, returns correct wait times, and shuts
  down cleanly:

  Ran 1 shell command (ctrl+o to expand)

● The pipeline works exactly as designed (validated with a simulated sink, no
  hardware needed):

  - Send-bound case (render 5 ms, send 20 ms): 20.6 ms/frame ≈ the max, vs 25 ms
  serial — overlap saved 87 ms over 20 frames. wait = 14.4 ms (the residual ≈
  send − render).
  - Render-bound case (render 20 ms, send 5 ms): 20.5 ms/frame, wait = 0.0 ms —
  the link is fully hidden.
  - No deadlock, all frames sent, clean shutdown.

  What I built

  A _SendPipe worker thread (__main__.py) that owns the SPI send. Each
  iteration: main renders+packs frame N, hands the payload to the worker via
  submit(), and immediately goes on to render N+1 while the worker transfers N.
  Depth-1 (one frame in flight), so latency rises by exactly one frame. Key
  detail: the worker is created before pin_to_core, so it inherits full-core
  affinity and the scheduler floats it onto an idle core while main stays pinned
  to the render core — that's what makes the overlap physical rather than two
  threads fighting over one core. Only the worker touches out, so
  spidev/gpiozero stay single-threaded; the GIL releases during the SPI write
  and READY wait, so it genuinely runs concurrent with the next render.

  The telemetry line changed — how to read it

  57.0 fps | render 9.9ms  pack 0.7ms  send 7.9ms  wait 0.4ms (SPI floor 6.6ms @
  40MHz, 32KB)
  - send = the worker's actual transfer time (the link cost, as before — compare
  to SPI floor).
  - wait = NEW: how long the main thread stalled on the previous send. wait ≈ 0 
  means the link is fully hidden behind render (render-bound — the good case).
  wait ≈ send means the link still paces you (send-bound).
  - Effective frame time ≈ render + pack + wait.

  Predicted effect on your shaders (all render-bound, so wait→0)

  ┌───────────┬────────┬───────────────────────┐
  │  Shader   │ Before │ After (≈ render+pack) │
  ├───────────┼────────┼───────────────────────┤
  │ will-blob │ 53 fps │ ~93 fps               │
  ├───────────┼────────┼───────────────────────┤
  │ golfing-6 │ 40 fps │ ~60 fps               │
  └───────────┴────────┴───────────────────────┘

  The 7.9 ms send vanishes from the critical path. The light shaders
  (will-circles, golfing-1) were already at the 60 fps cap — to see the true
  ceiling now, run --fps 240. That'll also make your heavier-shader samples next
  time much more informative.

  This properly completes the Pi-side stage: vectorized pack (kept, ~free now) +
  render∥send overlap (this). The PBO experiment is parked off-by-default with
  its findings documented. Files touched across the stage: hub75.py, egl.py,
  output.py, pipeline.py, __main__.py — these are the commits for main.

  When you're ready for the bench, Workstream 3 (parallel PIO) still eliminates
  send at the source (8 ms → <1 ms), which stacks with this — once the link is
  that fast, even the heaviest shaders are purely render-bound. Give it a run on
  the Pi and send the new render/send/wait lines — especially curious what wait
  reads on the heavy shaders (should be near zero).

✻ Cooked for 3m 42s

❯ /export                                                                       
  ⎿  Conversation copied to clipboard

※ recap: Goal: speed up the rayglow LED pipeline past its ~55fps wall. We landed vectorized pack and a render-while-send overlap on the Pi side. Next: run it on the Pi with --fps 240 and send the new render/send/wait telemetry lines.

❯  /export                                                                      

❯ /export                                                                       
  ⎿  Conversation copied to clipboard

❯ Wow. Well that was the single largest leap in performance I have seen in the  
  nearly two weeks I have been tinkering with different setups. It is now able  
  to run some shaders at viewable frame rates that were previously slideshows.  
  I plan to likely leave the default cap at 120 because the visual difference   
  beyond that is nearly imperceptible. Before begining Workflow 3, please       
  review /home/will/Projects/rayglow/firmware/src/bin/phase_experimental.rs vs  
  phase5_spi.rs. I tinkered with a few things in another session which were I   
  was trying to optimize for a single chain since I am still waiting on that    
  magical PCB to arrive here in 5 days. I am currently, and have been, running  
  the experimental firmware. For some reason I thought this was that was the    
  same session so I apologize for not giving you the heads up on that sooner.   
  Anyway, some great results. These samples are all much more intensive         
  fragment shaders. Amazing work be able to get these to run at all so, serious 
  kudos:                                                                        
                                                                                
  ~/presets/psychonautcave.glsl --spi-hz 40000000 --fps 120                     
    22.2 fps | render  43.9ms  pack  1.0ms  send   7.9ms  wait   0.0ms (SPI     
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  ~/presets/psychonautcave.glsl --spi-hz 40000000 --fps 120 --pbo               
    19.5 fps | render  50.4ms  pack  0.9ms  send   7.9ms  wait   0.0ms (SPI     
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  ~/presets/will-balls.glsl --spi-hz 40000000 --fps 120                         
    119.4 fps | render   5.9ms  pack  0.7ms  send   7.9ms  wait   0.0ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  ~/presets/will-balls.glsl --spi-hz 40000000 --fps 180                         
    125.9 fps | render   5.8ms  pack  0.6ms  send   7.9ms  wait   1.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  ~/presets/vertexknot.glsl --spi-hz 40000000 --fps 120                         
    53.2 fps | render  17.9ms  pack  0.8ms  send   7.9ms  wait   0.0ms (SPI     
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  ~/presets/neon_tunnel.glsl --spi-hz 40000000 --fps 120                        
    14.5 fps | render  68.1ms  pack  0.9ms  send   7.9ms  wait   0.0ms (SPI     
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  ~/rayglow/rayglow/render/presets/will-circles.glsl --spi-hz 40000000 --fps    
  240                                                                           
    125.9 fps | render   5.9ms  pack  0.6ms  send   7.9ms  wait   1.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
                                                                                
  I wanted to show something else I thought was interesting. This is that final 
  program running on rpi while I actively flashed the rp2350b with the same     
  phase-experimental firmware currently running. The screen paused on the frame 
  then picked right back up. I thought that little bit of reseliency that you   
  cooked it was worth a nod (again, that may have been you in another session,  
  but regardless):                                                              
                                                                                
    125.9 fps | render   5.9ms  pack  0.6ms  send   7.9ms  wait   1.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
    125.8 fps | render   5.9ms  pack  0.6ms  send   7.9ms  wait   1.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
     41.6 fps | render   6.0ms  pack  0.7ms  send  24.0ms  wait  17.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
    125.6 fps | render   5.9ms  pack  0.6ms  send   7.9ms  wait   1.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   
    125.7 fps | render   5.9ms  pack  0.6ms  send   7.9ms  wait   1.3ms (SPI    
  floor  6.6ms @ 40MHz, 32KB)                                                   

● Those numbers are genuinely exciting — and the telemetry is telling a
  beautiful story. The wait 0.0ms on every heavy shader (psychonautcave,
  neon_tunnel, vertexknot) confirms the link is now completely hidden — you're
  purely render-bound, exactly as predicted. And look at the lightest case:
  will-balls at --fps 180 settles at 125.9 fps with wait 1.3ms — that's the loop
  telling you the 7.9 ms link has become the ceiling again (render+pack = 6.5
  ms < send 7.9 ms), which is precisely the ~126 fps wall Workstream 3 will
  lift. The instrumentation is doing its job.

  (And that resilience clip — send 24ms / wait 17.3ms for one window while you
  reflashed, then snapping back to 125.9 — is the READY handshake + RX
  stall-timeout riding out the RP2350 dropping offline. Nice to see it hold.)

  Thanks for the heads-up on phase_experimental — that's exactly the kind of
  thing I need to know before Workstream 3, since the parallel-PIO fork must
  branch from the firmware you're actually running, not phase5. Let me diff them
  properly.

  Ran 1 shell command (ctrl+o to expand)

● Reviewed — and your telemetry actually confirms exactly which firmware you're
  on. Here's the picture.

  You're running single-chain, 4 panels — and the numbers prove it

  phase_experimental.rs has PANELS_IN_CHAIN = 4 → W = 256, single u8 cells →
  FRAME_BYTES = fb_cells(256,32,8) = 32768. That's the 32KB in every telemetry
  line, and 32768 × 8 / 40 MHz = 6.55 ms = your 6.6 ms SPI floor. Fully
  consistent. You're lighting a 256×32 wall (one serpentine row of 4 panels)
  while the PCB ships.

  The real diff (what actually changed vs phase5)

  The two files are ~95% identical. The substance is a geometry/cell-type swap,
  not a mechanism change:

  ┌─────────────┬──────────────────────────┬────────────────────────────────┐
  │             │ phase5_spi (two-chain,   │      phase_experimental        │
  │             │       production)        │      (single-chain, now)       │
  ├─────────────┼──────────────────────────┼────────────────────────────────┤
  │ Engine type │ hub75::Display +         │ hub75::single::Display1 +      │
  │             │ DisplayMemory<W,H,B>     │ single::DisplayMemory1         │
  ├─────────────┼──────────────────────────┼────────────────────────────────┤
  │ Cell        │ u16 (12 bits, both       │ u8 (6 bits, chain A only)      │
  │             │ chains)                  │                                │
  ├─────────────┼──────────────────────────┼────────────────────────────────┤
  │ FRAME_BYTES │ fb_cells·2 (64 KB @      │ fb_cells no ×2 (32 KB @        │
  │             │ 256×64)                  │ 256×32)                        │
  ├─────────────┼──────────────────────────┼────────────────────────────────┤
  │ RGB pins    │ GP0–11 (both chains)     │ GP0–5 only; GP6–11 bound but   │
  │             │                          │ black/unconnected              │
  ├─────────────┼──────────────────────────┼────────────────────────────────┤
  │ Pi packer   │ pack()                   │ pack_single() +                │
  │             │                          │ to_single_chain() fold         │
  ├─────────────┼──────────────────────────┼────────────────────────────────┤
  │ Your tuning │ OE_GAIN 14, DIV (4,0)    │ OE_GAIN 64, DIV (3,0) (your    │
  │             │                          │ single-chain knobs)            │
  └─────────────┴──────────────────────────┴────────────────────────────────┘

  The crucial fact for Workstream 3: the entire SPI-RX path is byte-for-byte 
  identical between the two — same PIO1 program, same DMA ch4 byte-ingest, same
  READY handshake, same RX_STALL_US stall-watchdog + abort_rx_dma (that's the
  "resilience you cooked" — it rode out your mid-run reflash by dropping the
  corrupt frame and re-arming on the next CS edge), same flip(). So the
  parallel-bus RX widening (in pins, 1 → in pins, 8) is the same delta whichever
  firmware it forks from; only the Display type / FRAME_BYTES / packer change.

  Two doc nits I'd fix while we're here

  - Stale frame-size comment: the A/B KNOB block says 8 = 512 wide, 128 KB; 4 = 
  256 wide, 64 KB. Those are the old u16 numbers — the actual u8 FRAME_BYTES is
  half (64 KB / 32 KB), as the very next comment block and your own 32KB
  telemetry confirm. The knob comment contradicts the code in the same file.
  - DATA_CLK_DIV MHz comments don't square with the (2,0)=37.5 MHz baseline
  stated elsewhere in the file — (3,0) should be ~25 MHz, not the "12.5 MHz" the
  comment claims. Worth a glance to confirm which is right (it's the internal
  pixel clock, harmless either way, just misleading).

  What this means for Workstream 3 — a real decision

  The PCB lands in ~5 days and flips you to the two-chain production wall
  (256×64, u16, 64 KB — where the link cost doubles to ~13 ms, making the
  parallel bus even more valuable). So WS3 ultimately serves two-chain, but you
  can't test two-chain until the PCB arrives. That's a fork:

  - Build WS3 on the current single-chain rig (my recommendation). You validate
  the whole parallel bus now on hardware you have — the C shim, the 8-lane
  wiring, the lane↔bit ordering on the LA, PioOut — with one variable changing
  at a time. Then when the PCB lands, flipping to two-chain is a small
  parametric swap (Display1→Display, u8→u16, pack_single→pack), not a fresh
  bring-up. The transport is already trusted.
  - Build WS3 directly for two-chain (PCB-ready, but blind). Ready the day the
  PCB arrives — but then you'd be debugging the parallel bus and the new PCB and
  two-chain geometry simultaneously. Too many new variables at once; classic
  bring-up trap.

  I'd strongly do the former: prove the parallel link on the single-chain rig
  this week, fold in two-chain when the PCB shows up. The phase6_parallel fork
  would branch from phase_experimental (your live firmware), and I'd structure
  it so the chain selection stays a parametric knob so it isn't throwaway.

  Want me to (a) fix those two doc nits now, and/or (b) start the Workstream-3
  scaffolding on that single-chain-first basis? I have everything I need from
  the review either way.

✻ Baked for 2m 24s

❯ Please proceed with both. My fault for not realizing I had jumped sessions.   
  At this point I worry humans hallucinate more than SoTA models. I'm pretty    
  sure I must have abruptly ended that session and not realized it. Using a     
  single orchestrator to edit the repo and not two independent agents unaware   
  the other is tinkering is messy. You are the sole editor at the moment so     
  yes, please update the docs to reflect the current state. I believe you are   
  correct about the 12.5MHz comment being antiquated. After those quick edits,  
  go ahead dive into phase6_parallel as you outlined.                           

● Both, on it. Let me read the full current firmware so my edits and the fork
  are precise.
  Read 1 file (ctrl+o to expand)

● The diff gave me everything. Let me make the three doc fixes first (the stale
  128 KB/u16 claims and the clock figure), then build the phase6_parallel fork.

● Update(src/bin/phase_experimental.rs)
  ⎿  Added 4 lines, removed 4 lines
      24  //! hardware-verified — nothing here is new firmware structure,
           only geometry.
      25  //!
      26  //! ## The cost (read before wiring)
      27 -//!   * **128 KB frame, not 64 KB.** The Phase-2 `u16` cell res
         -erves 6 bits for the                                           
      28 -//!     idle chain B, so `fb_cells(512,32,8)*2 = 131072`. Half 
         -the SPI payload is                                             
      29 -//!     zeros. The rpi5 `spidev` bufsiz is already 131072 (one 
         -transfer). A future                                            
      30 -//!     `u8` single-chain cell path would reclaim this; not wor
         -th it for bring-up.                                            
      27 +//!   * **64 KB frame at 8 panels (32 KB at 4).** This path use
         +s the single-chain                                             
      28 +//!     `u8` cell (`hub75::single`): one byte per (col, plane, 
         +addr-row), no idle                                             
      29 +//!     chain-B half, so `fb_cells(512,32,8) = 65536` — the two
         +-chain `×2` is gone.                                           
      30 +//!     The rpi5 `spidev` bufsiz (131072) covers it in one tran
         +sfer.                                                          
      31  //!   * **~½ the two-chain refresh**, because chain A now shift
          s 512 px/row instead
      32  //!     of 256 with no parallel chain B to hide behind. Drop `B
          ` to 7/6 if it
      33  //!     flickers below the ~150 Hz floor.

● Update(src/bin/phase_experimental.rs)
  ⎿  Added 2 lines, removed 2 lines
      88  const XTAL_FREQ_HZ: u32 = 12_000_000;
      89  
      90  // A/B KNOB — panels daisy-chained on the single chain. W = 64 
          * this.
      91 -//   8 = full wall   (512 wide, 128 KB frame)                  
      92 -//   4 = one panel row (256 wide, 64 KB frame)  ← for fps/SI A/
         -B testing                                                      
      91 +//   8 = full wall   (512 wide, 64 KB frame)   (u8 cells: fb_ce
         +lls(512,32,8))                                                 
      92 +//   4 = one panel row (256 wide, 32 KB frame)  ← for fps/SI A/
         +B testing                                                      
      93  // MUST match the Pi's `len(SPI_CHAIN_ORDER)` (config.py): both
           sides derive the
      94  // frame byte-count from it, and the handshake is a FIXED-size 
          contract (the RX
      95  // DMA waits for exactly FRAME_BYTES) — a mismatch desyncs the 
          link. Reflash to

● Update(src/bin/phase_experimental.rs)
  ⎿  Added 5 lines, removed 4 lines
       98  const W: usize = 64 * PANELS_IN_CHAIN;
       99  const H: usize = 32;
      100  const B: usize = 8;
      101 -// HUB75 pixel clock = sys_clk / (2*div). 8 panels in series i
          -s 2× the depth                                                
      102 -// phase3-row verified clean at (2,0)=37.5 MHz, AND the Adafru
          -it HAT adds an RC on                                          
      103 -// CLK — so start SLOW and ramp only after the wall behaves. (
          -6,0)=12.5 MHz.                                                
      104 -const DATA_CLK_DIV: (u16, u8) = (3, 0); // ~12.5 MHz pixel clo
          -ck (SI-safe to start)                                         
      101 +// HUB75 pixel clock = sys_clk / (2*div), sys_clk = 150 MHz. 8
          + panels in series is                                          
      102 +// 2× the depth phase3-row verified clean at (2,0)=37.5 MHz, A
          +ND the Adafruit HAT                                           
      103 +// adds an RC on CLK — so start SLOW and ramp only after the w
          +all behaves; (6,0) is                                         
      104 +// the 12.5 MHz floor if you need it.                         
      105 +const DATA_CLK_DIV: (u16, u8) = (3, 0); // ~25 MHz pixel clock
          + (150/(2*3))                                                  
      106  // Brightness gain (Phase 4 §set_oe_gain). 512-wide doubles th
           e per-plane shift
      107  // window vs 256, so there is MORE dead time to fill — the gai
           n ceiling roughly
      108  // doubles too (~16 before trading refresh). Start near the Ph
           ase-5 value, tune.

● Doc fixes done. Now let me check how bins are registered and whether the
  firmware toolchain is available here, so I can compile-check the fork:

  Ran 1 shell command (ctrl+o to expand)

● Toolchain's here (thumbv8m installed, nightly) and bins are explicitly
  registered — so I can compile-check the fork. Writing phase6_parallel.rs now
  (single-chain like your live firmware, with the SPI-RX widened to an 8-lane
  parallel bus; engine/DMA/loop/handshake all preserved verbatim):

● Write(src/bin/phase6_parallel.rs)
  ⎿  Wrote 402 lines to src/bin/phase6_parallel.rs
       1 //! Phase 6 — Pi 5 → RP2350 **8-lane parallel** link (Workstream
       2 //!
         chain scan-out
       4 //! engine, identical CS-framing + READY handshake, identical RX
       5 //! drop — the ONLY change is the ingest data path. Phase 5 / ex
       6 //! one MOSI bit per SCLK edge (8 SCLK edges per byte); this clo
       7 //! per clock edge = one whole byte per edge**, fed by the Pi 5'
         s RP1 PIO block.
       9 //! ## Why
          40 MHz is ~6.6 ms
     … +525 lines (ctrl+o to expand)

● Now register the bin and compile-check it:

● Update(Cargo.toml)
  ⎿  File must be read first

  Read 1 file (ctrl+o to expand)

● Update(Cargo.toml)
  ⎿  Added 4 lines
      45  name = "phase-experimental"
      46  path = "src/bin/phase_experimental.rs"
      47  
      48 +[[bin]]                                                        
      49 +name = "phase6-parallel"                                       
      50 +path = "src/bin/phase6_parallel.rs"                            
      51 +                                                               
      52  [dependencies]
      53  # RP2350 HAL. Features:
      54  #   binary-info          - picotool metadata block (.bi_entries
          )

  Ran 1 shell command (ctrl+o to expand)

● Firmware compiles clean (the only warnings are pre-existing dead-code in
  single.rs, untouched by me).

  Now the Pi side. The C shim is the linchpin and I can't compile it here (no
  libpio on the desktop), so let me read the exact piolib API surface — the
  instruction encoders and a real sideset+DMA example — to get it right from the
  headers rather than guess.

  Read 2 files (ctrl+o to expand)

● Good — I have the encoders. Let me grab the exact init pattern (config
  setters, pio_sm_init, DMA xfer signatures) from the ws2812 init and piolib
  header:

  Read 1 file, ran 1 shell command (ctrl+o to expand)

● I have the full API now. Let me confirm two things the shim depends on — that
  pio_select/current-pio handling exists (the sm_config_* helpers use it) and
  the pio_sm_config_xfer signature — then write the shim:

  Searched for 1 pattern (ctrl+o to expand)

● Everything confirmed. Writing the C shim, its Makefile, and a build README:

● Write(~/Projects/rayglow/rayglow/render/piobridge/pio_shim.c)
  ⎿  Wrote 126 lines to ../rayglow/render/piobridge/pio_shim.c
       1 // pio_shim.c — a thin flat-ABI shim over the Raspberry Pi 5 RP1
       2 //
         pio_gpio_init,
       4 // pio_sm_init, pio_encode_*, the DMA xfer helpers) is `static i
       5 // headers — there are no exported symbols for them, so a pure c
       6 // can't call them. This file compiles those calls into four rea
       7 // functions that `rayglow/render/pio_out.py` loads with ctypes.
       8 //
         s to the RP2350
         byte across 8
     … +153 lines (ctrl+o to expand)

● Write(~/Projects/rayglow/rayglow/render/piobridge/Makefile)
  ⎿  Wrote 22 lines to ../rayglow/render/piobridge/Makefile
       1 # Build libpioshim.so — the ctypes-loadable shim over RP1 piolib
       2 #
       3 # Needs piolib built as a library first (see README.md). Point P
       4 # piolib checkout (defaults to the in-repo reference copy). The 
         shim links the
         er,
       6 # pio_sm_xfer_data, ...) and inlines the static-inline config he
       7 # headers.
       8 #
      10 #   make PIOLIB=~/piolib # use a different piolib location
     … +16 lines (ctrl+o to expand)
● Write(~/Projects/rayglow/rayglow/render/piobridge/README.md)
  ⎿  Wrote 60 lines to ../rayglow/render/piobridge/README.md
       1 # piobridge — 8-lane parallel Pi 5 → RP2350 link (Workstream 3)
       3 Replaces the 1-lane SPI transport with an **8-lane source-synchr
         onous parallel
         he critical path
       5 entirely (a 32 KB frame goes from ~6.6 ms at 40 MHz SPI to well 
       6 
       7 The stream is **byte-identical** to the SPI path — same `hub75.p
       8 same CS-framing + READY handshake — so only the wire changes, no
         t the protocol.
      10 ## Pieces
     … +77 lines (ctrl+o to expand)

● Now PioOut (the SpiOut-compatible transport) and the --transport factory.
  Writing pio_out.py:

● Write(~/Projects/rayglow/rayglow/render/pio_out.py)
  ⎿  Wrote 83 lines to ../rayglow/render/pio_out.py
       1 """Parallel PIO transport — an SpiOut-compatible sink over the 8
       2 
         ` shape, so the
       4 `run_spi` loop and `_SendPipe` use it unchanged. Data + clock ar
       5 the Pi 5's RP1 PIO via `piobridge/libpioshim.so` (build it first
       6 `piobridge/README.md`); CS framing + READY use gpiozero, exactly
       7 byte stream is identical to the SPI path, so the firmware/packer
          don't change —
       9 
         e (and thus the
     … +94 lines (ctrl+o to expand)

● Now wire the factory and args into __main__.py:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 8 lines, removed 1 line
      321                      help="readback gamma (default 1.0 = LINEAR
           ; the rp2350b "
      322                           "firmware applies the CIE LUT, so cor
           recting here too "
      323                           "would double-correct)")
      324 +    ap.add_argument("--transport", choices=("spi", "pio"), def
          +ault="spi",                                                   
      325 +                    help="link to the rp2350b: 'spi' (1-lane, 
          +proven default) or "                                          
      326 +                         "'pio' (8-lane RP1-PIO parallel bus —
          + needs phase6 "                                               
      327 +                         "firmware + piobridge/libpioshim.so)"
          +)                                                             
      328      ap.add_argument("--spi-hz", type=int, default=24_000_000,
      325 -                    help="SPI clock in Hz (start low, then ram
          -p)")                                                          
      329 +                    help="SPI clock in Hz (--transport spi; st
          +art low, then ramp)")                                         
      330 +    ap.add_argument("--pio-clkdiv", type=float, default=4.0,  
      331 +                    help="RP1-PIO clock divisor (--transport p
          +io); per-lane rate "                                          
      332 +                         "≈ 200MHz/(2*div). Start high (slow),
          + then lower")                                                 
      333      ap.add_argument("--ready-gpio", type=int, default=25,
      334                      help="BCM pin reading the rp2350b READY li
           ne")
      335      ap.add_argument("--duration", type=float, default=0.0,

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 8 lines, removed 2 lines
      215      DMA, then pushes one 64 KB transfer.
      216      """
      217      from .hub75 import pack, pack_single, to_single_chain
      218 -    from .spi_out import SpiOut                               
      218  
      219      # Warm the full render+pack path before opening hardware (
           mirrors run_matrix).
      220      if feed:
     ...
      225      else:
      226          pack(warm)
      227  
      229 -    out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)      
      228 +    # Transport: the 1-lane SPI link (default) or the 8-lane R
          +P1-PIO parallel bus.                                          
      229 +    # Both expose send(bytes)/close(); the byte stream is iden
          +tical either way.                                             
      230 +    if args.transport == "pio":                               
      231 +        from .pio_out import PioOut                           
      232 +        out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.re
          +ady_gpio)                                                     
      233 +    else:                                                     
      234 +        from .spi_out import SpiOut                           
      235 +        out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)  
      236      # Build the send worker BEFORE pinning, so it inherits the
            full-core affinity
      237      # and floats onto an idle core; pin_to_core then pins only
            the render thread.
      238      pipe = _SendPipe(out)

● Now make the telemetry floor transport-aware:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 8 lines, removed 3 lines
      291                  # how much it leaked into the critical path. I
           f wait hugs 0 the
      292                  # link is fully hidden and `render` is the cla
           mp; if wait ~ send,
      293                  # the link still paces. SPI floor is the theor
           etical transfer min.
      294 -                spi_floor_ms = last_bytes * 8 / args.spi_hz * 
          -1e3                                                           
      294 +                if args.transport == "pio":                   
      295 +                    # 8 lanes, 1 byte/clock, 2 SM cycles/byte 
          +off RP1's 200 MHz.                                            
      296 +                    floor_ms = last_bytes / (200e6 / (2 * args
          +.pio_clkdiv)) * 1e3                                           
      297 +                    link = f"PIO floor {floor_ms:4.1f}ms @ clk
          +div {args.pio_clkdiv:g}"                                      
      298 +                else:                                         
      299 +                    floor_ms = last_bytes * 8 / args.spi_hz * 
          +1e3                                                           
      300 +                    link = f"SPI floor {floor_ms:4.1f}ms @ {ar
          +gs.spi_hz/1e6:.0f}MHz"                                        
      301                  send_ms = pipe.acc_send / n * 1e3
      302                  print(f"{n / (now - fps_t):6.1f} fps | "
      303                        f"render {acc_render / n * 1e3:5.1f}ms  
           "
      304                        f"pack {acc_pack / n * 1e3:4.1f}ms  "
      305                        f"send {send_ms:5.1f}ms  wait {acc_wait 
           / n * 1e3:5.1f}ms "
      300 -                      f"(SPI floor {spi_floor_ms:4.1f}ms @ {ar
          -gs.spi_hz/1e6:.0f}MHz, "                                      
      301 -                      f"{last_bytes//1024}KB)")               
      306 +                      f"({link}, {last_bytes//1024}KB)")      
      307                  fps_frames, fps_t = 0, now
      308                  acc_render = acc_pack = acc_wait = 0.0
      309                  pipe.acc_send = 0.0

● Let me verify the Python side parses and the CLI wires up (the PioOut import
  is lazy, so this works on the desktop):

  Ran 1 shell command (ctrl+o to expand)

● Everything checks out on the desktop: Python compiles, the new
  --transport/--pio-clkdiv args are wired, and the SPI default path is untouched
  (dry-run still renders). Combined with the clean firmware build earlier, the
  Workstream-3 scaffolding is complete. Let me leave a breadcrumb in memory
  noting the parallel link now exists as the intermediate transport step.

  Recalled 1 memory, wrote 2 memories (ctrl+o to expand)

● Done — both the doc fixes and the full Workstream-3 scaffolding are in. Here's
  the state.

  Doc fixes (phase_experimental.rs)

  Corrected the three stale claims to match the live u8 single-chain reality:
  the "128 KB / u16 ×2" cost note → 64 KB/32 KB u8, the A/B-KNOB byte counts,
  and (3,0) → ~25 MHz (not 12.5). The file is now internally consistent with
  what your telemetry shows.

  Workstream 3 — scaffolded (single-chain-first, as agreed)

  ┌────────────┬──────────────────────────────────────┬─────────────────────┐
  │   Piece    │                 File                 │       Status        │
  ├────────────┼──────────────────────────────────────┼─────────────────────┤
  │ Firmware:  │ firmware/src/bin/phase6_parallel.rs  │ compiles clean      │
  │ 8-lane RX  │ (+ Cargo.toml)                       │ (cargo build --bin  │
  │            │                                      │ phase6-parallel)    │
  ├────────────┼──────────────────────────────────────┼─────────────────────┤
  │ C shim     │                                      │ written to the      │
  │ over RP1   │ rayglow/render/piobridge/pio_shim.c  │ confirmed API;      │
  │ piolib     │                                      │ builds on the Pi    │
  ├────────────┼──────────────────────────────────────┼─────────────────────┤
  │ Build +    │ piobridge/Makefile,                  │ pin map + bring-up  │
  │ docs       │ piobridge/README.md                  │ order               │
  ├────────────┼──────────────────────────────────────┼─────────────────────┤
  │ Pi         │ rayglow/render/pio_out.py (PioOut)   │ compiles,           │
  │ transport  │                                      │ SpiOut-compatible   │
  ├────────────┼──────────────────────────────────────┼─────────────────────┤
  │ Factory +  │ rayglow/render/__main__.py           │ wired; SPI default  │
  │ telemetry  │ (--transport, --pio-clkdiv)          │ unregressed         │
  └────────────┴──────────────────────────────────────┴─────────────────────┘

  It forks your live phase_experimental (single-chain u8), keeps the CS-framing
  + READY handshake + stall-watchdog identical, and only widens the ingest from
  in pins, 1 to in pins, 8. The byte stream is unchanged, so hub75.py/verify.py
  are untouched. The two-chain swap (for when the PCB lands) is marked at the
  three SINGLE-CHAIN lines in the firmware.

  What I could and couldn't verify from here

  - Verified on the desktop: firmware compiles; Python compiles;
  --transport/--pio-clkdiv parse; the SPI path still renders (dry-run). The shim
  is written against the exact piolib signatures I confirmed in the headers
  (encoders, sm_config_*, pio_sm_config_xfer/xfer_data, pio_select).
  - Can't verify here (no Pi): the shim compiles/links (needs libpio on the Pi),
  and the link runs end-to-end. That's bench work.

  Your bench bring-up (in piobridge/README.md, order matters)

  1. Build piolib as a lib: cd .reference/rpi5/utils/piolib && cmake 
  -DBUILD_SHARED_LIBS=1 . && make
  2. Build the shim: cd rayglow/render/piobridge && make
  3. Wire the 8 lanes + DCLK/CS/READY per the pin table (RP2350 GP20–27 data,
  GP28 clk, GP29 cs, GP19 ready ↔ Pi GPIO12–19/20/21/25).
  4. Flash: cd firmware && cargo run --bin phase6-parallel
  5. Logic-analyzer first, slow clock (--pio-clkdiv 16): send a 0x00,0x01,… ramp
  and confirm byte/lane order before any panel output. This is the one thing
  that can't be proven from the desk — the convention is byte bit i → lane i (Pi
  out shift-right + RP2350 in pins,8 DATA0=bit0). If bytes land bit-reversed or
  lane-swapped, that's where it shows.
  6. Then on-panel vs SPI (must be pixel-identical), then lower --pio-clkdiv and
  watch rx fps/drops.

  --transport spi stays the proven fallback the whole time.

  Git grouping

  Two natural commits: the doc fixes (phase_experimental.rs), and the WS3 
  scaffolding (phase6_parallel.rs, Cargo.toml, piobridge/*, pio_out.py,
  main.py). The latter is the feature-branch work — feat/pio-parallel-link —
  kept behind the spi default so main stays runnable.

  When you're at the bench and hit something on the LA, send me the byte dump
  and I'll help reconcile the lane ordering — that's the one spot I'd expect a
  first-try surprise.

✻ Crunched for 12m 7s

※ recap: Goal: speed up the rayglow LED-wall pipeline. We landed the Pi-side render wins and scaffolded the 8-lane parallel PIO link (firmware, shim, PioOut all compile). Next: on the Pi, build piolib and the shim, then logic-analyzer-validate lane order at a slow clock. (disable recaps in /config)

❯ I've run into an issue while wiring: GP20 through GP27 are connected, there   
  is no exposed GP28 or GP29 pin on the rp2350b. GP27 is the highest GP number  
  pin.                                                                          

● Ah — that's the constraint that matters. Your board only breaks out GP0–GP27
  (28 pins), and the scan-out engine already occupies GP0–18 (12 RGB + 4 addr +
  clk/lat/oe). That leaves only GP19–27 (9 pins) for the link. Eight data lanes
  (GP20–27) would eat eight of those nine, leaving just GP19 for the three
  control signals (DCLK, CS, READY) — impossible. 8 lanes simply doesn't fit
  this board.

  The fix: drop to 4 lanes. That fits comfortably (4 data + 3 control = 7 of 9),
  divides cleanly into a byte (2 clocks/byte, no awkward bit-straddling that 6
  lanes would cause), and is still a big win — at clkdiv 2 that's 4 lanes × 50
  MHz = 200 Mbit/s → a 32 KB frame in ~1.3 ms, far under your render time. The
  link still leaves the critical path entirely.

  New pin map, chosen to preserve most of your wiring — your GP20–23 data wires
  stay data; GP24–26 (which you'd already run as data) just get repurposed to
  control:

  ┌─────────┬────────────┬─────────────┐
  │ signal  │ RP2350b GP │  rpi5 BCM   │
  ├─────────┼────────────┼─────────────┤
  │ DATA0–3 │ GP20–23    │ GPIO12–15   │
  ├─────────┼────────────┼─────────────┤
  │ DCLK    │ GP24       │ GPIO20      │
  ├─────────┼────────────┼─────────────┤
  │ CS      │ GP25       │ GPIO21      │
  ├─────────┼────────────┼─────────────┤
  │ READY   │ GP26 (out) │ GPIO25 (in) │
  ├─────────┼────────────┼─────────────┤
  │ spare   │ GP19, GP27 │ —           │
  └─────────┴────────────┴─────────────┘

  So on the RP2350 side you keep GP20–23 as data, and GP24/25/26 become
  DCLK/CS/READY. READY back on GP26 even matches the old SPI pin. Let me update
  the firmware, shim, and docs — and I caught a real bug while reworking the bit
  math (more below).

● Write(src/bin/phase6_parallel.rs)
  ⎿  Added 61 lines, removed 71 lines
       1 -//! Phase 6 — Pi 5 → RP2350 **8-lane parallel** link (Workstrea
         -m 3).                                                          
       1 +//! Phase 6 — Pi 5 → RP2350 **4-lane parallel** link (Workstrea
         +m 3).                                                          
       2  //!
       3  //! A widened sibling of `phase_experimental`: identical single
          -chain scan-out
       4  //! engine, identical CS-framing + READY handshake, identical R
          X DMA + framebuffer
       5  //! drop — the ONLY change is the ingest data path. Phase 5 / e
          xperimental clock
       6 -//! one MOSI bit per SCLK edge (8 SCLK edges per byte); this cl
         -ocks **8 data lanes                                            
       7 -//! per clock edge = one whole byte per edge**, fed by the Pi 5
         -'s RP1 PIO block.                                              
       6 +//! one MOSI bit per SCLK edge (8 edges/byte); this clocks **4 
         +data lanes per edge                                            
       7 +//! = a nibble per edge, 2 edges/byte**, fed by the Pi 5's RP1 
         +PIO block.                                                     
       8  //!
       9 -//! ## Why                                                     
      10 -//! The 1-lane SPI link is the throughput wall: a 32 KB frame a
         -t 40 MHz is ~6.6 ms                                            
      11 -//! (~150 fps ceiling), and it only gets worse on the two-chain
         - wall (64 KB). With                                            
      12 -//! 8 lanes the same byte count clocks in 8× faster at the same
         - per-lane rate, so                                             
      13 -//! the link stops being the limit and the wall is purely rende
         -r-bound. The Pi                                                
      14 -//! side drives the bus with its own PIO (RP1, 200 MHz) + DMA —
         - see                                                           
      15 -//! `rayglow/render/pio_out.py` and the `pio_shim.c` it loads. 
         -CS framing + READY                                             
      16 -//! are unchanged, so the protocol is the same; only the width 
         -grew 1 → 8.                                                    
       9 +//! ## Why 4 lanes (not 8)                                     
      10 +//! The board exposes GP0–27 only. The scan-out engine owns GP0
         +–18, leaving GP19–27                                           
      11 +//! (9 pins) for the link; 8 data lanes + DCLK + CS + READY = 1
         +1 won't fit. 4 lanes                                           
      12 +//! (4 data + 3 control = 7) fits with room, divides a byte cle
         +anly (2 nibbles), and                                          
      13 +//! still lifts the link off the critical path: at the Pi's clk
         +div 2 that's                                                   
      14 +//! 4×50 MHz = 200 Mbit/s → a 32 KB frame in ~1.3 ms (vs ~6.6 m
         +s over 40 MHz SPI).                                            
      15  //!
      16  //! ## Byte order (why the stream stays byte-identical)
      19 -//! `in pins, 8` samples lanes DATA0..7 with DATA0 = bit 0; aut
         -opush at 8 bits = one                                          
      20 -//! byte per clock, byte-size DMA, in order. The Pi presents by
         -te bit i on lane i                                             
      21 -//! (`out pins, 8`, LSB-first), bytes in memory order — so the 
         -framebuffer ends up                                            
      22 -//! byte-for-byte equal to the same `hub75.py` packer stream th
         -e SPI path uses.                                               
      23 -//! **Nail this on a logic analyzer first** with a 0x00,0x01,0x
         -02… ramp payload: if                                           
      24 -//! bytes arrive bit-reversed or lane-swapped, flip the shift d
         -irection or lane                                               
      25 -//! wiring. (This is the one thing that can't be proven from th
         -e desk.)                                                       
      17 +//! `in pins, 4` samples lanes DATA0..3 = one nibble; autopush 
         +at 8 = two samples                                             
      18 +//! per byte. Both ends shift **right**: the Pi (`out pins, 4`,
         + shift-right,                                                  
      19 +//! autopull 32) emits each byte LOW nibble first with lane i =
         + nibble bit i, bytes                                           
      20 +//! in memory order; this RX (in shift-right, autopush 8) reass
         +embles them to the                                             
      21 +//! exact `hub75.py::pack_single` stream. **Validate on a logic
         + analyzer first**                                              
      22 +//! with a 0x00,0x01,0x02… ramp: if nibbles swap, flip ONE shif
         +t direction; if bits                                           
      23 +//! within a nibble mirror, reverse the lane wiring. (Can't be 
         +proven from the desk.)                                         
      24  //!
      25  //! ## Pin map  (RP2350b GP ↔ rpi5 BCM ↔ signal)
      28 -//!     DATA0..7  GP20..GP27   ← rpi5 GPIO12..GPIO19   (8 lanes
         -, IN base = GP20)                                              
      29 -//!     DCLK      GP28         ← rpi5 GPIO20           (Pi-driv
         -en data clock)                                                 
      30 -//!     CS        GP29         ← rpi5 GPIO21           (active-
         -low frame boundary)                                            
      31 -//!     READY     GP19         → rpi5 GPIO25 (input)   (RP2350 
         -→ Pi: armed)                                                   
      32 -//!     common GND (ideally a return beside each lane bundle — 
         -8 lanes at speed on                                            
      33 -//!     flying wire ring badly; keep them short and ground-refe
         -renced).                                                       
      34 -//! Scan-out engine pins are unchanged (GP0–18). DATA0..7 must 
         -stay CONTIGUOUS                                                
      35 -//! (the `in pins, 8` group); DCLK/CS are read by absolute `wai
         -t gpio` so they are                                            
      26 +//!     DATA0..3  GP20..GP23   ← rpi5 GPIO12..GPIO15   (4 lanes
         +, IN base = GP20)                                              
      27 +//!     DCLK      GP24         ← rpi5 GPIO20           (Pi-driv
         +en data clock)                                                 
      28 +//!     CS        GP25         ← rpi5 GPIO21           (active-
         +low frame boundary)                                            
      29 +//!     READY     GP26         → rpi5 GPIO25 (input)   (RP2350 
         +→ Pi: armed)                                                   
      30 +//!     common GND (a return beside the lane bundle; keep short
         + — lanes at speed on                                           
      31 +//!     flying wire ring badly). GP19 + GP27 are spare.        
      32 +//! Scan-out engine pins are unchanged (GP0–18). DATA0..3 must 
         +stay CONTIGUOUS                                                
      33 +//! (the `in pins, 4` group); DCLK/CS are read by absolute `wai
         +t gpio` so they are                                            
      34  //! hardcoded in the PIO program below — keep the consts in syn
          c with the literals.
      37 -//! Confirm GP20–29 carry no board-variant special function bef
         -ore soldering.                                                 
      35  //!
      36  //! ## Two-chain swap (when the custom HAT lands)
      37  //! This binary is single-chain (`hub75::single`, u8 cells) to 
          match the current
     ...
      73  pub static PICOTOOL_ENTRIES: [hal::binary_info::EntryAddr; 3] =
           [
      74      hal::binary_info::rp_cargo_bin_name!(),
      75      hal::binary_info::rp_cargo_version!(),
      79 -    hal::binary_info::rp_program_description!(c"RP2350 RGB driv
         -er - Phase 6 parallel 8-lane link"),                           
      76 +    hal::binary_info::rp_program_description!(c"RP2350 RGB driv
         +er - Phase 6 parallel 4-lane link"),                           
      77  ];
      78  
      79  const XTAL_FREQ_HZ: u32 = 12_000_000;
     ...
      106  // trips it. Runs only during the idle ingest wait → no steady
           -state cost.
      107  const RX_STALL_US: u32 = 50_000; // 50 ms with zero byte progr
           ess = dead transfer
      108  
      112 -// Parallel-link GPIO. DATA0 is the PIO IN base; the 8 lanes D
          -ATA0..7 must be                                               
      113 -// CONTIGUOUS (GP20..GP27). DCLK and CS are sampled with absol
          -ute `wait gpio`, so                                           
      109 +// Parallel-link GPIO. DATA0 is the PIO IN base; the 4 lanes D
          +ATA0..3 must be                                               
      110 +// CONTIGUOUS (GP20..GP23). DCLK and CS are sampled with absol
          +ute `wait gpio`, so                                           
      111  // they are hardcoded in the PIO program below — keep these co
           nsts in sync.
      115 -const DATA0_PIN: u8 = 20; // 8 lanes: GP20..GP27              
      116 -const DCLK_PIN: u8 = 28; // Pi-driven data clock              
      117 -const CS_PIN: u8 = 29; // chip-select, active low — frame boun
          -dary                                                          
      118 -const READY_PIN: u8 = 19; // RP2350 -> Pi, armed-and-waiting  
      119 -const _: () = assert!(DATA0_PIN == 20, "PIO `in pins, 8` base 
          -must match DATA0_PIN");                                       
      120 -const _: () = assert!(DCLK_PIN == 28, "PIO `wait gpio 28` must
          - match DCLK_PIN");                                            
      121 -const _: () = assert!(CS_PIN == 29, "PIO `wait gpio 29` must m
          -atch CS_PIN");                                                
      112 +const DATA0_PIN: u8 = 20; // 4 lanes: GP20..GP23              
      113 +const NUM_LANES: u8 = 4;                                      
      114 +const DCLK_PIN: u8 = 24; // Pi-driven data clock              
      115 +const CS_PIN: u8 = 25; // chip-select, active low — frame boun
          +dary                                                          
      116 +const READY_PIN: u8 = 26; // RP2350 -> Pi, armed-and-waiting (
          +same pin as SPI READY)                                        
      117 +const _: () = assert!(DATA0_PIN == 20, "PIO `in pins, 4` base 
          +must match DATA0_PIN");                                       
      118 +const _: () = assert!(DCLK_PIN == 24, "PIO `wait gpio 24` must
          + match DCLK_PIN");                                            
      119 +const _: () = assert!(CS_PIN == 25, "PIO `wait gpio 25` must m
          +atch CS_PIN");                                                
      120  
      121  static mut DISPLAY_BUFFER: hub75::single::DisplayMemory1<W, H,
            B> =
      122      hub75::single::DisplayMemory1::new(); // SINGLE-CHAIN (two
           -chain: DisplayMemory)
     ...
      205      display.set_oe_gain(OE_GAIN);
      206  
      207      // --- Parallel-RX pin setup -----------------------------
           ---------------
      210 -    // 8 data lanes + DCLK into PIO1 with pull-down (E9 backst
          -op, idle low). CS is                                          
      211 -    // active-low, so it gets a pull-UP (idles high between fr
          -ames). Bind them so                                           
      212 -    // the pads stay routed to PIO1 for the program's lifetime
          -.                                                             
      213 -    let _data: [_; 8] = [                                     
      208 +    // 4 data lanes (GP20..23) + DCLK into PIO1 with pull-down
          + (E9 backstop, idle                                           
      209 +    // low). CS is active-low, so it gets a pull-UP (idles hig
          +h between frames).                                            
      210 +    // Bind them so the pads stay routed to PIO1 for the progr
          +am's lifetime.                                                
      211 +    let _data: [_; 4] = [                                     
      212          pins.gpio20.into_function::<FunctionPio1>().into_pull_
           type::<PullDown>().into_dyn_pin(),
      213          pins.gpio21.into_function::<FunctionPio1>().into_pull_
           type::<PullDown>().into_dyn_pin(),
      214          pins.gpio22.into_function::<FunctionPio1>().into_pull_
           type::<PullDown>().into_dyn_pin(),
      215          pins.gpio23.into_function::<FunctionPio1>().into_pull_
           type::<PullDown>().into_dyn_pin(),
      218 -        pins.gpio24.into_function::<FunctionPio1>().into_pull_
          -type::<PullDown>().into_dyn_pin(),                            
      219 -        pins.gpio25.into_function::<FunctionPio1>().into_pull_
          -type::<PullDown>().into_dyn_pin(),                            
      220 -        pins.gpio26.into_function::<FunctionPio1>().into_pull_
          -type::<PullDown>().into_dyn_pin(),                            
      221 -        pins.gpio27.into_function::<FunctionPio1>().into_pull_
          -type::<PullDown>().into_dyn_pin(),                            
      216      ];
      217      let _dclk = pins
      224 -        .gpio28                                               
      218 +        .gpio24                                               
      219          .into_function::<FunctionPio1>()
      220          .into_pull_type::<PullDown>();
      221      let _cs = pins
      228 -        .gpio29                                               
      222 +        .gpio25                                               
      223          .into_function::<FunctionPio1>()
      224          .into_pull_type::<PullUp>();
      225      // READY is a plain push-pull output (SIO), idle low until
            a frame is armed.
      232 -    let mut ready = pins.gpio19.into_push_pull_output();      
      226 +    let mut ready = pins.gpio26.into_push_pull_output();      
      227      let _ = ready.set_low();
      228  
      235 -    // --- Parallel-RX PIO program (CS-framed, 8 lanes/clock) 
          -----------------                                              
      229 +    // --- Parallel-RX PIO program (CS-framed, 4 lanes/clock) 
          +----------------                                              
      230      // Frame boundary = the CS edge, NOT a byte count (immune 
           to idle-line noise +
      231      // handshake jitter). Per frame: wait CS high (idle) then 
           CS low (fresh start),
      238 -    // then on each DCLK rising edge sample all 8 lanes → auto
          -push at 8 bits = one                                          
      239 -    // byte per clock → byte-size DMA → in-order framebuffer b
          -ytes. `restart()`                                             
      240 -    // (each frame, CPU) re-parks at the CS preamble with shif
          -t counter 0, so a                                             
      241 -    // dropped frame can't desync bit/byte alignment across fr
          -ames.                                                         
      232 +    // then on each DCLK rising edge sample all 4 lanes (a nib
          +ble) → autopush at                                            
      233 +    // 8 bits = one byte per TWO clocks → byte-size DMA → in-o
          +rder framebuffer                                              
      234 +    // bytes. `restart()` (each frame, CPU) re-parks at the CS
          + preamble with shift                                          
      235 +    // counter 0, so a dropped frame can't desync nibble/byte 
          +alignment.                                                    
      236      let program = pio::pio_asm!(
      237          ".wrap_target",
      244 -        "wait 1 gpio 29", // CS high  — idle / previous frame 
          -ended                                                         
      245 -        "wait 0 gpio 29", // CS low   — fresh frame start, shi
          -ft counter = 0                                                
      246 -        "byteloop:",                                          
      247 -        "wait 1 gpio 28", // DCLK rising = sample point       
      248 -        "in pins, 8",     // sample DATA0..7 (IN base = GP20),
          - DATA0 = bit 0                                                
      249 -        "wait 0 gpio 28", // DCLK falling                     
      250 -        "jmp byteloop",   // next byte (restart() re-parks at 
          -the CS preamble)                                              
      238 +        "wait 1 gpio 25", // CS high  — idle / previous frame 
          +ended                                                         
      239 +        "wait 0 gpio 25", // CS low   — fresh frame start, shi
          +ft counter = 0                                                
      240 +        "nibloop:",                                           
      241 +        "wait 1 gpio 24", // DCLK rising = sample point       
      242 +        "in pins, 4",     // sample DATA0..3 (IN base = GP20) 
          += one nibble                                                  
      243 +        "wait 0 gpio 24", // DCLK falling                     
      244 +        "jmp nibloop",    // next nibble (restart() re-parks a
          +t the CS preamble)                                            
      245          ".wrap",
      246      );
      247      let installed = pio1.install(&program.program).unwrap();
      248      let (mut rx_sm, rx_fifo, _tx) = PIOBuilder::from_installed
           _program(installed)
      249          .in_pin_base(DATA0_PIN)
      256 -        .in_shift_direction(ShiftDirection::Left) // DATA0=bit
          -0, MSB-lane shifts in last                                    
      250 +        .in_shift_direction(ShiftDirection::Right) // matches 
          +the Pi's shift-right out                                      
      251          .autopush(true)
      258 -        .push_threshold(8) // one byte per DCLK edge (8 lanes)
      252 +        .push_threshold(8) // one byte per TWO nibble samples 
      253          .buffers(Buffers::OnlyRx)
      254          .clock_divisor_fixed_point(1, 0) // full system clock
      255          .build(rx_sm);
      262 -    // All sampled pins are inputs: the 8 data lanes + DCLK + 
          -CS.                                                           
      256 +    // All sampled pins are inputs: the 4 data lanes + DCLK + 
          +CS.                                                           
      257      rx_sm.set_pindirs([
      258          (DATA0_PIN, PinDir::Input),
      259          (DATA0_PIN + 1, PinDir::Input),
      260          (DATA0_PIN + 2, PinDir::Input),
      261          (DATA0_PIN + 3, PinDir::Input),
      268 -        (DATA0_PIN + 4, PinDir::Input),                       
      269 -        (DATA0_PIN + 5, PinDir::Input),                       
      270 -        (DATA0_PIN + 6, PinDir::Input),                       
      271 -        (DATA0_PIN + 7, PinDir::Input),                       
      262          (DCLK_PIN, PinDir::Input),
      263          (CS_PIN, PinDir::Input),
      264      ]);
     ...
      268      let rx_dreq = rx_fifo.dreq_value();
      269  
      270      info!(
      281 -        "phase6-parallel: {}x{} SINGLE-CHAIN wall (u8 cells, c
          -hain A). 8-lane CS-framed RX on PIO1 (DATA0 GP{}..GP{}, DCLK G
          -P{}, CS GP{}), READY GP{}. frame = {} bytes.",                
      271 +        "phase6-parallel: {}x{} SINGLE-CHAIN wall (u8 cells, c
          +hain A). 4-lane CS-framed RX on PIO1 (DATA0 GP{}..GP{}, DCLK G
          +P{}, CS GP{}), READY GP{}. frame = {} bytes.",                
      272          W,
      273          H,
      274          DATA0_PIN,
      285 -        DATA0_PIN + 7,                                        
      275 +        DATA0_PIN + NUM_LANES - 1,                            
      276          DCLK_PIN,
      277          CS_PIN,
      278          READY_PIN,
     ...
      295          let dst = display.inactive_fb_ptr() as u32;
      296  
      297          // Fresh alignment: drain any stale RX byte and restar
           t the SM so its
      308 -        // shift counter is 0 → the next DCLK edge is byte 0 o
          -f this frame. Then                                            
      309 -        // arm the DMA to drain the FIFO into `dst`.          
      298 +        // shift counter is 0 → the next DCLK edge is the firs
          +t nibble of this                                              
      299 +        // frame. Then arm the DMA to drain the FIFO into `dst
          +`.                                                            
      300          sm.clear_fifos();
      301          sm.restart();
      302          arm_rx_dma(&rx_ch, fifo_addr, dst, FRAME_BYTES, rx_dre
           q);

● Now the shim — change to 4 lanes and fix the autopull threshold bug I caught
  while reworking the bit math: it must be 32 (one full DMA word = 8 nibbles),
  not 8. With threshold 8 the SM would refill the OSR after a single byte and
  discard the other 3 bytes of every 32-bit word — only ¼ of the frame would
  actually transmit. (That bug was latent in the 8-lane version too; good thing
  the rewire surfaced it.)

● Update(~/Projects/rayglow/rayglow/render/piobridge/pio_shim.c)
  ⎿  Added 14 lines, removed 10 lines
       6  // can't call them. This file compiles those calls into four re
          al exported
       7  // functions that `rayglow/render/pio_out.py` loads with ctypes
          .
       8  //
       9 -// What it does: drives an 8-lane source-synchronous parallel b
         -us to the RP2350                                               
      10 -// (firmware bin `phase6-parallel`): `out pins, 8` presents one
         - byte across 8                                                 
      11 -// GPIO while a 1-bit sideset toggles a data clock the RP2350 s
         -amples on. DMA                                                 
      12 -// feeds the bytes (zero CPU during the burst). CS framing + RE
         -ADY handshake are                                              
      13 -// driven from Python (gpiozero) exactly as the SPI path does. 
       9 +// What it does: drives a 4-lane source-synchronous parallel bu
         +s to the RP2350                                                
      10 +// (firmware bin `phase6-parallel`): `out pins, 4` presents one
         + nibble across 4                                               
      11 +// GPIO while a 1-bit sideset toggles a data clock the RP2350 s
         +amples on; two                                                 
      12 +// nibbles = one byte. DMA feeds the bytes (zero CPU during the
         + burst). CS framing                                            
      13 +// + READY handshake are driven from Python (gpiozero) exactly 
         +as the SPI path.                                               
      14 +// (4 lanes, not 8: the board exposes GP0–27 and the scan-out e
         +ngine owns GP0–18,                                             
      15 +// leaving only GP19–27 for the link — see phase6_parallel.rs.)
      16  //
      17  // Build on the Pi (needs piolib built + installed as a shared 
          lib, see Makefile):
      18  //   cc -shared -fPIC -O2 pio_shim.c -lpio -I<piolib>/include -
          o libpioshim.so
      19  //
      18 -// Lane/bit convention (must match firmware/src/bin/phase6_para
         -llel.rs): byte bit                                             
      19 -// i -> lane i (DATA_i). Pi out: shift_right + autopull(8) => L
         -SB drives lane 0.                                              
      20 -// RP2350 in: `in pins,8` with DATA0 = bit 0. Validate with a 0
         -x00,0x01,... ramp                                              
      21 -// on a logic analyzer before trusting on-panel output.        
      20 +// Lane/bit convention (must match firmware/src/bin/phase6_para
         +llel.rs): both ends                                            
      21 +// shift RIGHT. Pi out (shift_right, autopull 32) emits each by
         +te LOW nibble first                                            
      22 +// with lane i = nibble bit i, bytes in memory order; RP2350 `i
         +n pins,4` (shift                                               
      23 +// right, autopush 8) reassembles the byte. Validate with a 0x0
         +0,0x01,... ramp on                                             
      24 +// a logic analyzer before trusting on-panel output (if nibbles
         + swap, flip ONE                                                
      25 +// shift direction; if bits within a nibble mirror, reverse the
         + lane wiring).                                                 
      26  
      27  #include <stdint.h>
      28  #include <stdlib.h>
      29  
      30  #include "piolib.h"
      31  
      28 -#define NUM_LANES 8                                            
      32 +#define NUM_LANES 4                                            
      33  
      34  struct pioshim {
      35      PIO pio;

● Update(~/Projects/rayglow/rayglow/render/piobridge/pio_shim.c)
  ⎿  Added 4 lines, removed 3 lines
      56      }
      57  
      58      // TX program (.side_set 1):
      59 -    //   out pins, 8   side 0   ; drive a byte on the 8 lanes, 
         -clock LOW                                                      
      59 +    //   out pins, 4   side 0   ; drive a nibble on the 4 lanes
         +, clock LOW                                                    
      60      //   nop           side 1   ; clock HIGH — RP2350 samples o
          n this rising edge
      61 -    // autopull (threshold 8) refills the OSR a byte at a time 
         -straight from DMA;                                             
      62 -    // when the FIFO drains the SM stalls on `out` holding side
         - 0 (clock idles low).                                          
      61 +    // autopull (threshold 32) consumes a whole 32-bit DMA word
         + = 8 nibbles =                                                 
      62 +    // 4 bytes before refilling; when the FIFO drains the SM st
         +alls on `out`                                                  
      63 +    // holding side 0 (clock idles low).                       
      64      static uint16_t instrs[2];
      65      instrs[0] = (uint16_t)(pio_encode_out(pio_pins, NUM_LANES) 
          | pio_encode_sideset(1, 0));
      66      instrs[1] = (uint16_t)(pio_encode_nop() | pio_encode_sidese
          t(1, 1));

● Update(~/Projects/rayglow/rayglow/render/piobridge/pio_shim.c)  ⎿  Added 5 lines, removed 3 lines
      87      sm_config_set_sideset(&c, 1, false, false);   // 1 sideset 
          bit = the data clock
      88      sm_config_set_sideset_pins(&c, clk_pin);
      89      sm_config_set_out_pins(&c, data0_pin, NUM_LANES);
      90 -    // shift_right=true => OSR LSB drives lane 0 (byte bit i ->
         - lane i); autopull                                             
      91 -    // on; threshold 8 => one byte consumed per `out pins, 8`. 
      92 -    sm_config_set_out_shift(&c, true, true, 8);                
      90 +    // shift_right=true => OSR LSB drives lane 0, LOW nibble of
         + each byte first;                                              
      91 +    // autopull on; threshold 32 => consume a full DMA word (4 
         +bytes / 8 nibbles)                                             
      92 +    // before refilling, so every byte of the frame is sent (th
         +reshold 8 would                                                
      93 +    // emit only 1 byte per 32-bit word and drop the other 3). 
      94 +    sm_config_set_out_shift(&c, true, true, 32);               
      95      sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
      96      sm_config_set_clkdiv(&c, clkdiv);
      97      pio_sm_init(pio, sm, offset, &c);

● Now the README pin table and lane references:

● Update(~/Projects/rayglow/rayglow/render/piobridge/README.md)
  ⎿  Added 6 lines, removed 3 lines
       1 -# piobridge — 8-lane parallel Pi 5 → RP2350 link (Workstream 3)
       1 +# piobridge — 4-lane parallel Pi 5 → RP2350 link (Workstream 3)
       2  
       3 -Replaces the 1-lane SPI transport with an **8-lane source-synch
         -ronous parallel                                                
       3 +Replaces the 1-lane SPI transport with a **4-lane source-synchr
         +onous parallel                                                 
       4  bus** driven by the Pi 5's RP1 PIO block, lifting the link off 
          the critical path
       5 -entirely (a 32 KB frame goes from ~6.6 ms at 40 MHz SPI to well
         - under 1 ms).                                                  
       5 +(a 32 KB frame goes from ~6.6 ms at 40 MHz SPI to ~1.3 ms at cl
         +kdiv 2).                                                       
       6  
       7 +(4 lanes, not 8: the RP2350 board exposes GP0–27 and the scan-o
         +ut engine owns                                                 
       8 +GP0–18, leaving only GP19–27 for the link — see `phase6_paralle
         +l.rs`.)                                                        
       9 +                                                               
      10  The stream is **byte-identical** to the SPI path — same `hub75.
          py` packer output,
      11  same CS-framing + READY handshake — so only the wire changes, n
          ot the protocol.
      12  

● Update(~/Projects/rayglow/rayglow/render/piobridge/README.md)
  ⎿  Added 7 lines, removed 7 lines
      21  ## Pin map (BCM ↔ RP2350 GP)
      22  | signal      | rpi5 BCM        | RP2350b GP   | notes |
      23  |-------------|-----------------|--------------|-------|
      24 -| DATA0..7    | GPIO12..GPIO19  | GP20..GP27   | 8 contiguous l
         -anes, `out pins,8` base = GPIO12 |                             
      25 -| DCLK        | GPIO20          | GP28         | Pi-driven data
         - clock (sideset) |                                             
      26 -| CS          | GPIO21          | GP29         | active-low fra
         -me boundary (gpiozero output) |                                
      27 -| READY       | GPIO25 (in)     | GP19 (out)   | RP2350 → Pi: a
         -rmed-and-waiting |                                             
      28 -| GND         | —               | —            | a return besid
         -e each lane bundle; keep short |                               
      24 +| DATA0..3    | GPIO12..GPIO15  | GP20..GP23   | 4 contiguous l
         +anes, `out pins,4` base = GPIO12 |                             
      25 +| DCLK        | GPIO20          | GP24         | Pi-driven data
         + clock (sideset) |                                             
      26 +| CS          | GPIO21          | GP25         | active-low fra
         +me boundary (gpiozero output) |                                
      27 +| READY       | GPIO25 (in)     | GP26 (out)   | RP2350 → Pi: a
         +rmed-and-waiting (same GP as SPI) |                            
      28 +| GND         | —               | —            | a return besid
         +e the lane bundle; keep short |                                
      29  
      30 -Confirm none of GPIO12–21 are reserved on your rig (PWM/I2S/PCM
         - can claim some)                                               
      31 -and that GP20–29 carry no board-variant special function before
         - soldering.                                                    
      30 +GP19 + GP27 are spare on the RP2350. Confirm none of GPIO12–15/
         +20/21 are reserved                                             
      31 +on your Pi (PWM/I2S/PCM can claim some) before wiring.         
      32  
      33  ## Build & run (on the Pi)
      34  1. Build piolib as a library (once):

● Let me catch any remaining 8-lane references in the README and recompile the
  firmware:

  Searched for 1 pattern (ctrl+o to expand)

● Update(~/Projects/rayglow/rayglow/render/pio_out.py)
  ⎿  Added 1 line, removed 1 line
      1 -"""Parallel PIO transport — an SpiOut-compatible sink over the 8
        --lane RP1-PIO bus.                                              
      1 +"""Parallel PIO transport — an SpiOut-compatible sink over the 4
        +-lane RP1-PIO bus.                                              
      2  
      3  Drop-in for `SpiOut`: same `__init__` / `send(bytes)` / `close()
         ` shape, so the
      4  `run_spi` loop and `_SendPipe` use it unchanged. Data + clock ar
         e clocked out by

● Update(~/Projects/rayglow/rayglow/render/pio_out.py)
  ⎿  Added 1 line, removed 1 line
      48          self.ready = DigitalInputDevice(ready_bcm, pull_up=Fals
          e)
      49          self.cs = DigitalOutputDevice(cs_gpio, active_high=Fals
          e,
      50                                        initial_value=False)
      51 -        print(f"pio_out: 8-lane PIO bus — DATA0=GPIO{data0_gpio
         -}, "                                                           
      51 +        print(f"pio_out: 4-lane PIO bus — DATA0=GPIO{data0_gpio
         +}, "                                                           
      52                f"CLK=GPIO{clk_gpio}, CS=GPIO{cs_gpio}, READY=GPI
          O{ready_bcm}, "
      53                f"clkdiv={self.clkdiv:g}")
      54  

● Update(~/Projects/rayglow/rayglow/render/piobridge/README.md)
  ⎿  Added 1 line, removed 1 line
      12  
      13  ## Pieces
      14  - `pio_shim.c` → `libpioshim.so` — flat C ABI over RP1 `piolib`
           (whose API is
      15 -  `static inline`, so ctypes can't call it directly). Drives `o
         -ut pins, 8` + a                                                
      15 +  `static inline`, so ctypes can't call it directly). Drives `o
         +ut pins, 4` + a                                                
      16    sideset data clock, DMA-fed.
      17  - `rayglow/render/pio_out.py` — `PioOut`, the `SpiOut`-compatib
          le transport that
      18    loads the shim and frames each burst with CS (gpiozero) + REA
          DY.

  Ran 1 shell command (ctrl+o to expand)

● Hold on — while finishing the bit math I need to correct the RX shift
  direction I just set, and there's a subtlety worth getting right. The byte 
  must land in ISR[7:0] for the byte-size DMA to read it — that's exactly how
  the proven single-lane path works (ShiftLeft + threshold 8). ShiftRight would
  leave the assembled byte in the high bits and the byte-DMA would read zeros.
  So RX must stay ShiftLeft like the single-lane code; the nibble ordering is
  then the Pi's job. Let me revert that and handle the nibble order on the Pi
  side with a toggle (since the exact ordering is the one thing only the LA can
  confirm).

● Update(src/bin/phase6_parallel.rs)
  ⎿  Added 9 lines, removed 6 lines
      15  //!
      16  //! ## Byte order (why the stream stays byte-identical)
      17  //! `in pins, 4` samples lanes DATA0..3 = one nibble; autopush 
          at 8 = two samples
      18 -//! per byte. Both ends shift **right**: the Pi (`out pins, 4`,
         - shift-right,                                                  
      19 -//! autopull 32) emits each byte LOW nibble first with lane i =
         - nibble bit i, bytes                                           
      20 -//! in memory order; this RX (in shift-right, autopush 8) reass
         -embles them to the                                             
      21 -//! exact `hub75.py::pack_single` stream. **Validate on a logic
         - analyzer first**                                              
      22 -//! with a 0x00,0x01,0x02… ramp: if nibbles swap, flip ONE shif
         -t direction; if bits                                           
      23 -//! within a nibble mirror, reverse the lane wiring. (Can't be 
         -proven from the desk.)                                         
      18 +//! per byte. RX shifts **left** (like the proven single-lane p
         +ath) so the byte                                               
      19 +//! lands in ISR[7:0] where the byte-size DMA reads it — first 
         +nibble sampled →                                               
      20 +//! HIGH nibble. So the Pi must send the HIGH nibble of each by
         +te first: it does a                                            
      21 +//! cheap per-byte nibble-swap then `out pins, 4` shift-right (
         +autopull 32, byte                                              
      22 +//! order preserved). Net: the framebuffer ends byte-identical 
         +to                                                             
      23 +//! `hub75.py::pack_single`. **Validate on a logic analyzer fir
         +st** with a                                                    
      24 +//! 0x00,0x01,0x02… ramp: if every byte's nibbles are swapped, 
         +toggle the Pi's                                                
      25 +//! `nibble_swap` (PioOut); if bits within a nibble mirror, rev
         +erse the lane                                                  
      26 +//! wiring. (This ordering can't be proven from the desk.)     
      27  //!
      28  //! ## Pin map  (RP2350b GP ↔ rpi5 BCM ↔ signal)
      29  //!     DATA0..3  GP20..GP23   ← rpi5 GPIO12..GPIO15   (4 lanes
          , IN base = GP20)

● Update(src/bin/phase6_parallel.rs)
  ⎿  Added 4 lines, removed 1 line
      250      let installed = pio1.install(&program.program).unwrap();
      251      let (mut rx_sm, rx_fifo, _tx) = PIOBuilder::from_installed
           _program(installed)
      252          .in_pin_base(DATA0_PIN)
      253 -        .in_shift_direction(ShiftDirection::Right) // matches 
          -the Pi's shift-right out                                      
      253 +        // ShiftLeft so the assembled byte lands in ISR[7:0] f
          +or the byte DMA (the                                          
      254 +        // proven single-lane placement); first nibble sampled
          + becomes the HIGH                                             
      255 +        // nibble, so the Pi sends high-nibble-first (PioOut n
          +ibble_swap).                                                  
      256 +        .in_shift_direction(ShiftDirection::Left)             
      257          .autopush(true)
      258          .push_threshold(8) // one byte per TWO nibble samples
      259          .buffers(Buffers::OnlyRx)

● Now add the nibble_swap toggle to PioOut (default on per the derivation;
  flippable at the bench) and a --pio-no-nibble-swap CLI escape hatch:

● Update(~/Projects/rayglow/rayglow/render/pio_out.py)
  ⎿  Added 8 lines, removed 1 line
      13  import ctypes
      14  import os
      15  
      16 +# Per-byte nibble swap (high<->low). The RX shifts left so the 
         +FIRST nibble it                                                
      17 +# samples becomes the byte's HIGH nibble; the Pi sends low-nibb
         +le-first under                                                 
      18 +# shift-right, so we pre-swap to make the framebuffer byte-iden
         +tical to the                                                   
      19 +# packer. bytes.translate applies this 256-entry LUT in C (negl
         +igible per frame,                                              
      20 +# and on the send worker thread so it overlaps the next render)
         +.                                                              
      21 +_NIBBLE_SWAP = bytes(((b << 4) | (b >> 4)) & 0xFF for b in rang
         +e(256))                                                        
      22  
      23 +                                                               
      24  class PioOut:
      25      _LIB = "libpioshim.so"
      26  
      20 -    def __init__(self, clkdiv=4.0, ready_bcm=25,               
      27 +    def __init__(self, clkdiv=4.0, ready_bcm=25, nibble_swap=Tr
         +ue,                                                            
      28                   data0_gpio=12, clk_gpio=20, cs_gpio=21, lib_pa
          th=None):
      29          from gpiozero import DigitalInputDevice, DigitalOutputD
          evice
      30  

● Update(~/Projects/rayglow/rayglow/render/pio_out.py)
  ⎿  Added 1 line
      45          self._lib.pioshim_close.argtypes = [ctypes.c_void_p]
      46  
      47          self.clkdiv = float(clkdiv)
      48 +        self.nibble_swap = bool(nibble_swap)                   
      49          self.data0_gpio = int(data0_gpio)
      50          self.clk_gpio = int(clk_gpio)
      51          self._handle = None

● Update(~/Projects/rayglow/rayglow/render/pio_out.py)
  ⎿  Added 2 lines
      74      def send(self, payload):
      75          """Wait for READY, frame the burst with CS, clock the b
          ytes out."""
      76          self._ensure_open(len(payload))
      77 +        if self.nibble_swap:                                   
      78 +            payload = payload.translate(_NIBBLE_SWAP)          
      79          self.ready.wait_for_active()        # RP2350 armed its 
          RX DMA
      80          self.cs.on()                        # CS low — frame st
          art
      81          rc = self._lib.pioshim_send(self._handle, payload, len(
          payload))

● Now wire the bench escape-hatch flag and update the shim/README convention
  notes:

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 4 lines
      341      ap.add_argument("--pio-clkdiv", type=float, default=4.0,
      342                      help="RP1-PIO clock divisor (--transport p
           io); per-lane rate "
      343                           "≈ 200MHz/(2*div). Start high (slow),
            then lower")
      344 +    ap.add_argument("--pio-no-nibble-swap", action="store_true
          +",                                                            
      345 +                    help="(--transport pio) disable the per-by
          +te nibble swap — "                                            
      346 +                         "use only if the logic analyzer shows
          + nibbles arriving "                                           
      347 +                         "un-swapped")                        
      348      ap.add_argument("--ready-gpio", type=int, default=25,
      349                      help="BCM pin reading the rp2350b READY li
           ne")
      350      ap.add_argument("--duration", type=float, default=0.0,

● Update(~/Projects/rayglow/rayglow/render/__main__.py)
  ⎿  Added 2 lines, removed 1 line
      229      # Both expose send(bytes)/close(); the byte stream is iden
           tical either way.
      230      if args.transport == "pio":
      231          from .pio_out import PioOut
      232 -        out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.re
          -ady_gpio)                                                     
      232 +        out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.re
          +ady_gpio,                                                     
      233 +                     nibble_swap=not args.pio_no_nibble_swap) 
      234      else:
      235          from .spi_out import SpiOut
      236          out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)
● Update(~/Projects/rayglow/rayglow/render/piobridge/pio_shim.c)
  ⎿  Added 8 lines, removed 6 lines
      17  // Build on the Pi (needs piolib built + installed as a shared 
          lib, see Makefile):
      18  //   cc -shared -fPIC -O2 pio_shim.c -lpio -I<piolib>/include -
          o libpioshim.so
      19  //
      20 -// Lane/bit convention (must match firmware/src/bin/phase6_para
         -llel.rs): both ends                                            
      21 -// shift RIGHT. Pi out (shift_right, autopull 32) emits each by
         -te LOW nibble first                                            
      22 -// with lane i = nibble bit i, bytes in memory order; RP2350 `i
         -n pins,4` (shift                                               
      23 -// right, autopush 8) reassembles the byte. Validate with a 0x0
         -0,0x01,... ramp on                                             
      24 -// a logic analyzer before trusting on-panel output (if nibbles
         - swap, flip ONE                                                
      25 -// shift direction; if bits within a nibble mirror, reverse the
         - lane wiring).                                                 
      20 +// Lane/bit convention (must match firmware/src/bin/phase6_para
         +llel.rs): this SM                                              
      21 +// shifts RIGHT (out pins,4, autopull 32) — low nibble first, l
         +ane i = nibble bit                                             
      22 +// i, bytes in memory order. The RP2350 RX shifts LEFT (byte la
         +nds in ISR[7:0] for                                            
      23 +// the byte DMA), so its first-sampled nibble is the HIGH one; 
         +`pio_out.py` does a                                            
      24 +// per-byte nibble swap before calling send() so the framebuffe
         +r comes out                                                    
      25 +// byte-identical. Validate with a 0x00,0x01,... ramp on a logi
         +c analyzer (if every                                           
      26 +// byte's nibbles are swapped, toggle PioOut.nibble_swap; if bi
         +ts within a nibble                                             
      27 +// mirror, reverse the lane wiring).                           
      28  
      29  #include <stdint.h>
      30  #include <stdlib.h>

● Recompile firmware and verify the Python, then tidy the README's bring-up
  note:

  Ran 1 shell command (ctrl+o to expand)

● Everything rebuilds clean. Let me update the README bring-up note to mention
  the nibble-order check + the runtime toggle:

● Update(~/Projects/rayglow/rayglow/render/piobridge/README.md)
  ⎿  Added 7 lines, removed 3 lines
      53  
      54  ## Bring-up order (don't skip)
      55  1. **Logic-analyzer first.** At a high `--pio-clkdiv` (slow), s
          end a known ramp and
      56 -   confirm byte/lane order on the RP2350 side (a debug `info!` 
         -of the first                                                   
      57 -   framebuffer cells, or picotool). This pins the lane↔bit conv
         -ention (byte bit i                                             
      58 -   → lane i) before any panel output is trusted.               
      56 +   confirm the bytes land correctly on the RP2350 side (a debug
         + `info!` of the                                                
      57 +   first framebuffer cells, or picotool). Two things to confirm
         +:                                                              
      58 +   - **Nibble order** — each byte goes out as two nibbles (low 
         +then high under the                                            
      59 +     default swap). If every byte's nibbles come out swapped, r
         +un with                                                        
      60 +     `--pio-no-nibble-swap` (no reflash needed).               
      61 +   - **Lane↔bit** — within a nibble, lane i = bit i (DATA0 = bi
         +t 0). If mirrored,                                             
      62 +     reverse the physical lane wiring (DATA0↔DATA3, DATA1↔DATA2
         +).                                                             
      63  2. **Then on-panel**, compare a static test pattern against the
           SPI path — they
      64     must be pixel-identical (same bytes). Then lower `--pio-clkd
          iv` toward 1 and
      65     watch `rx fps` / drops; the READY handshake self-paces exact
          ly as SPI did.

● Let me fix the memory note that still says "8-lane":

  Wrote 1 memory (ctrl+o to expand)

● All updated and rebuilding clean. Here's what changed and why.

  The fix: 4 lanes (8 didn't fit your board)

  Your board's GP0–27, minus the engine's GP0–18, leaves only GP19–27 for the
  link. Eight data lanes would need 8 of those 9 pins, leaving one for three
  control signals — impossible. 4 lanes fits cleanly (4 data + DCLK + CS + READY
  = 7), divides a byte into two nibbles without straddling, and still crushes
  the bottleneck: at --pio-clkdiv 2 that's 4×50 MHz = 200 Mbit/s → ~1.3 ms for a
  32 KB frame (vs 6.6 ms over 40 MHz SPI). The link still leaves the critical
  path.

  New pin map (preserves most of your wiring — GP20–23 stay data; GP24–26, which
  you'd already run, become control):

  ┌─────────┬───────────┬───────────┐
  │ signal  │ RP2350 GP │  Pi BCM   │
  ├─────────┼───────────┼───────────┤
  │ DATA0–3 │ GP20–23   │ GPIO12–15 │
  ├─────────┼───────────┼───────────┤
  │ DCLK    │ GP24      │ GPIO20    │
  ├─────────┼───────────┼───────────┤
  │ CS      │ GP25      │ GPIO21    │
  ├─────────┼───────────┼───────────┤
  │ READY   │ GP26      │ GPIO25    │
  └─────────┴───────────┴───────────┘

  So on the bench: keep your GP20–23 data wires, repurpose GP24/25/26 to
  DCLK/CS/READY, drop GP27. GP19+GP27 spare.

  A real bug I caught while reworking the math

  The shim's autopull threshold was 8; it must be 32. The RP1 DMA feeds the SM
  32-bit words (4 bytes); with threshold 8 the SM refills the OSR after one byte
  and discards the other 3 bytes of every word — only ¼ of each frame would
  ever transmit. Fixed to 32 (= one full word = 8 nibbles). That bug was latent
  in the 8-lane version too; the rewire surfaced it.

  The nibble-order subtlety (the one thing the LA must settle)

  Splitting a byte across 4 lanes adds a nibble-ordering axis the single-lane
  path didn't have. My reasoning, encoded as the default:
  - RX shifts left — same as the proven single-lane path, so the assembled byte
  lands in ISR[7:0] where the byte-size DMA reads it. (ShiftRight would leave it
  in the high bits and the DMA would read zeros — all black.)
  - That means the first nibble sampled becomes the byte's high nibble. The Pi,
  shifting right for natural byte order, sends the low nibble first — so PioOut
  does a per-byte nibble swap (a C-speed bytes.translate, on the worker thread)
  so the framebuffer comes out byte-identical.
  - If my derivation is off by a swap (this is genuinely hardware-dependent),
  there's a no-reflash escape hatch: --pio-no-nibble-swap. The README's bring-up
  step 1 now spells out the two LA checks (nibble order → toggle the flag;
  lane↔bit mirror → swap DATA0↔DATA3 wiring).

  Status

  - Firmware (phase6_parallel.rs, 4-lane, ShiftLeft): rebuilds clean.
  - Shim/PioOut/README/CLI: updated to 4 lanes, threshold-32 fix, nibble-swap
  default + toggle; Python compiles, --pio-no-nibble-swap wired.
  - Memory + docs corrected from 8→4.

  Rewire to the 4-lane map, rebuild the shim, and do the LA ramp first — that's
  where the nibble/lane order gets locked in five minutes. Send me the ramp
  capture (or the first few RX framebuffer bytes) and I'll confirm whether the
  default nibble-swap is right or you want --pio-no-nibble-swap.

✻ Sautéed for 11m 56s

※ recap: Goal: replace rayglow's SPI link with a faster 4-lane parallel PIO bus. The phase6 firmware and Pi-side code are written and compile. Next: rewire to the 4-lane pin map, rebuild the shim on the Pi, and run the logic-analyzer ramp test. (disable recaps in /config)

❯ Okay rewire is done, shim rebuilt on the raspberry pi, and the rp2350 is      
  flashed and should be accessible via probe-rs. Nothing is printing in the     
  terminal after the flash so I may have done something off. Please proceed     
  with checking the nibble-order or let me know if I need to recheck something. 

● First things first: "nothing printing" is almost certainly the RTT/defmt 
  session, not your wiring or the flash — so let me get logs flowing before we
  touch nibble order, and I'll add a byte-dump diagnostic so the order check
  becomes a single command.

  The key thing about defmt: those info! lines only appear when a host tool is
  actively reading the RP2350's RTT buffer. A bare flash (probe-rs download, or
  cargo flash) writes the firmware and resets, but nothing is listening, so you
  see nothing. You need an attached session:

  cd firmware
  cargo run --bin phase6-parallel      # builds + flashes + streams defmt-rtt
  # or, against the already-flashed target:  probe-rs attach --chip RP235X
  target/.../phase6-parallel

  With a session attached you should immediately see the boot line —
  phase6-parallel: 256x32 SINGLE-CHAIN wall ... frame = 32768 bytes. — and then
  rx fps 0 (drops 0) once a second (0 because the Pi isn't sending yet). Two
  quick checks while you're there:
  - Is the panel scanning black (engine alive) or totally dark/frozen? phase6
  inits the scan-out engine exactly like phase_experimental, so it should look
  like an idle phase_experimental.
  - If you get a panic line instead, paste it — that's a different problem.

  Now let me make the nibble check trivial. I'm adding a byte-dump to the
  firmware (logs the first 8 received bytes each second) and a ramp sender on
  the Pi, so you send a known 0,1,2,3,… and read back exactly what landed.

● Update(src/bin/phase6_parallel.rs)
  ⎿  Added 5 lines
      288      let mut frames: u32 = 0;
      289      let mut drops: u32 = 0;
      290      let mut last_us: u32 = timer.get_counter_low();
      291 +    // Bring-up aid: address of the most recently received fra
          +me, so the per-second                                         
      292 +    // telemetry can dump its first bytes for the nibble/lane-
          +order check against a                                         
      293 +    // known ramp from `tools/pio_ramp.py`. Set false once the
          + link is trusted.                                             
      294 +    const RX_DEBUG_BYTES: bool = true;                        
      295 +    let mut last_good: u32 = 0;                               
      296      let mut sm = rx_sm.start();
      297  
      298      // Stolen handle to the DMA block's global CHAN_ABORT regi
           ster, used only to

● Update(src/bin/phase6_parallel.rs)
  ⎿  Added 22 lines
      348              // flip avoids commit()'s racy `fb_loop_busy` wait
            that deadlocks under
      349              // this tight streaming cadence (see Display::flip
           ).
      350              display.flip();
      351 +            last_good = dst; // buffer just filled (now on scr
          +een) — safe to read                                           
      352          }
      353  
      354          frames += 1;
      355          let now = timer.get_counter_low();
      356          if now.wrapping_sub(last_us) >= 1_000_000 {
      357              info!("rx fps {} (drops {})", frames, drops);
      358 +            if RX_DEBUG_BYTES && last_good != 0 {             
      359 +                // First 8 received bytes. Against a 0,1,2,3,…
          + ramp: correct =                                              
      360 +                // 00 01 02 03 04 05 06 07; nibble-swapped = 0
          +0 10 20 30 40 50 60 70.                                       
      361 +                let p = last_good as *const u8;               
      362 +                let b = unsafe {                              
      363 +                    [                                         
      364 +                        p.read_volatile(),                    
      365 +                        p.add(1).read_volatile(),             
      366 +                        p.add(2).read_volatile(),             
      367 +                        p.add(3).read_volatile(),             
      368 +                        p.add(4).read_volatile(),             
      369 +                        p.add(5).read_volatile(),             
      370 +                        p.add(6).read_volatile(),             
      371 +                        p.add(7).read_volatile(),             
      372 +                    ]                                         
      373 +                };                                            
      374 +                info!(                                        
      375 +                    "  rx[0..8] = {:#04x} {:#04x} {:#04x} {:#0
          +4x} {:#04x} {:#04x} {:#04x} {:#04x}",                         
      376 +                    b[0], b[1], b[2], b[3], b[4], b[5], b[6], 
          +b[7]                                                          
      377 +                );                                            
      378 +            }                                                 
      379              frames = 0;
      380              drops = 0;
      381              last_us = now;

● Now the ramp sender, then rebuild the firmware:

● Write(~/Projects/rayglow/tools/pio_ramp.py)
  ⎿  Wrote 53 lines to ../tools/pio_ramp.py
       1 """Send a raw byte ramp over the 4-lane PIO parallel link — nibb
       2 
       3 Run on the Pi with the `phase6-parallel` firmware flashed and an
       4 attached (`cargo run --bin phase6-parallel`). This streams a kno
       5 0,1,2,3,… bytes; the firmware logs the first 8 received bytes ea
         ch second.
       7 Read the firmware's `rx[0..8]` line:
       8   00 01 02 03 04 05 06 07   -> correct, the default is right.
         -no-nibble-swap.
         e the lane wiring
     … +55 lines (ctrl+o to expand)

  Ran 1 shell command (ctrl+o to expand)

● Both compile clean. One correction to the ramp tool's usage — since you must
  launch from a local cwd (/tmp, for lgpio) but tools/ isn't part of the
  installed package, it has to be run by path, not -m:

● Update(~/Projects/rayglow/tools/pio_ramp.py)
  ⎿  Added 4 lines, removed 3 lines
      11                                                            (DATA
          0<->DATA3, DATA1<->DATA2).
      12  
      13  Launch from a LOCAL cwd (e.g. /tmp), not the ~/rayglow mount — 
          lgpio's FIFO can't
      14 -live on the network mount. Match --bytes to the firmware FRAME_
         -BYTES (32 KB for a                                             
      15 -4-panel single chain; the handshake is a fixed-size contract). 
      14 +live on the network mount. Run by PATH (tools/ isn't part of th
         +e installed                                                    
      15 +package; rayglow is, so the import resolves anywhere). Match --
         +bytes to the                                                   
      16 +firmware FRAME_BYTES (32 KB for a 4-panel single chain; fixed-s
         +ize handshake).                                                
      17  
      18      cd /tmp
      18 -    sudo ~/venv/bin/python -m tools.pio_ramp --pio-clkdiv 16   
      19 +    sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-cl
         +kdiv 16                                                        
      20  """
      21  import argparse
      22  import time

● Everything's built and ready. Since the hardware's at your bench and I'm on
  the desktop, here's the exact sequence to run — then paste me the output and
  I'll read off the nibble order.

  1. Get logs + the new diagnostic flashed (one command)

  Your currently-flashed firmware predates the byte-dump, so reflash and attach
  RTT in one go:
  cd firmware
  cargo run --bin phase6-parallel
  cargo run rebuilds (now with the byte-dump), flashes, and streams defmt-rtt.
  You should see:
  phase6-parallel: 256x32 SINGLE-CHAIN wall ... frame = 32768 bytes.
  rx fps 0 (drops 0)        ← once a second, 0 until the Pi sends
  - If you still see nothing, it's the probe session, not the firmware. Check
  probe-rs list (is the probe enumerated?), that .cargo/config.toml's runner is
  probe-rs run, and the SWD/SWCLK wiring to the RP2350. A panic line instead →
  paste it.

  2. Send the ramp from the Pi (second terminal)

  cd /tmp
  sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-clkdiv 16
  (Slow clock — clkdiv 16 — on purpose for bring-up.) Watch the firmware
  terminal: rx fps should jump above 0, and a new line appears:
    rx[0..8] = 0x00 0x01 0x02 ...

  3. Paste me rx[0..8] + the rx fps/drops line. The decode:

  ┌─────────────────────┬───────────────┬──────────────────────────────────┐
  │   rx[0..8] reads    │    meaning    │              action              │
  ├─────────────────────┼───────────────┼──────────────────────────────────┤
  │ 00 01 02 03 04 05   │ correct —     │ lower --pio-clkdiv, then run the │
  │ 06 07               │ default is    │  real renderer with --transport  │
  │                     │ right         │ pio                              │
  ├─────────────────────┼───────────────┼──────────────────────────────────┤
  │ 00 10 20 30 40 50   │ nibbles       │ rerun with --pio-no-nibble-swap  │
  │ 60 70               │ swapped       │ (and pass it to the renderer)    │
  ├─────────────────────┼───────────────┼──────────────────────────────────┤
  │ 00 08 04 0c 02 0a … │ lane order    │ swap the physical lanes          │
  │  (bits mirrored in  │ reversed      │ DATA0↔DATA3, DATA1↔DATA2         │
  │ each nibble)        │               │                                  │
  ├─────────────────────┼───────────────┼──────────────────────────────────┤
  │ all 0x00, or rx fps │ not receiving │ handshake/clock — see below      │
  │  stays 0            │               │                                  │
  └─────────────────────┴───────────────┴──────────────────────────────────┘

  If rx fps stays 0 while the ramp runs, the RP2350 isn't clocking data in —
  check the three control lines: READY (RP2350 GP26 → Pi GPIO25), CS (Pi GPIO21
  → GP25), DCLK (Pi GPIO20 → GP24). If READY never reads active on the Pi,
  pio_ramp blocks at wait_for_active and prints nothing after the banner — that
  alone tells us READY is the culprit.

  This is the one genuinely hardware-determined step in the whole project, so
  don't be surprised if it's the swapped-nibble row — that's a one-flag fix, no
  reflash. Paste the bytes and we'll lock it.

✻ Crunched for 3m 41s

※ recap: Goal: replace rayglow's SPI link with a faster 4-lane parallel bus to the RP2350. The firmware, shim, and ramp tool are built and flashed. Next: run `cargo run --bin phase6-parallel`, send the ramp from the Pi, and paste me the `rx[0..8]` bytes. (disable recaps in /config)

❯ Very real chance I have the wiring incorrect after the reconfig. If the       
  likely outcome of a mismatch is the following let me know:                    
                                                                                
  ~/Projects/rayglow/firmware main* ⇡                                           
  ❯ cargo run --bin phase6-parallel                                             
  warning: fields `fb_loop_ch` and `benchmark` are never read                   
    --> src/single.rs:87:5                                                      
     |                                                                          
  80 | pub struct Display1<'a, CH1, const W: usize, const H: usize, const B:    
  usize, C, const ADDR_PINS:...                                                 
     |            -------- fields in this struct                                
  ...                                                                           
  87 |     fb_loop_ch: Channel<CH1>,                                            
     |     ^^^^^^^^^^                                                           
  88 |     benchmark: bool,                                                     
     |     ^^^^^^^^^                                                            
     |                                                                          
     = note: `#[warn(dead_code)]` (part of `#[warn(unused)]`) on by default     
                                                                                
  warning: `rp2350-rgb-driver` (lib) generated 1 warning                        
      Finished `dev` profile [optimized + debuginfo] target(s) in 0.09s         
  warning: the following packages contain code that will be rejected by a       
  future version of Rust: proc-macro-error2 v2.0.1                              
  note: to see what the problems were, use the option                           
  `--future-incompat-report`, or run `cargo report future-incompatibilities     
  --id 1`                                                                       
       Running `probe-rs run --chip RP235x                                      
  target/thumbv8m.main-none-eabihf/debug/phase6-parallel`                       
        Erasing ✔ 100% [####################]  88.00 KiB @  72.24 KiB/s (took   
  1s)                                                                           
    Programming ✔ 100% [####################]  88.00 KiB @  41.51 KiB/s (took   
  2s)                                                                           
   Finished in 3.45s                                                            
  [INFO ] phase6-parallel: 256x32 SINGLE-CHAIN wall (u8 cells, chain A). 4-lane 
  CS-framed RX on PIO1 (DATA0 GP20..GP23, DCLK GP24, CS GP25), READY GP26.      
  frame = 32768 bytes. (phase6_parallel src/bin/phase6_parallel.rs:276)         
  ^CReceived Ctrl+C, exiting                                                    
  Exited by user request                                                        
                                                                                
  ~/Projects/rayglow/firmware main* ⇡ 1m 31s                                    
  ❯ probe-rs list                                                               
  The following debug probes were found:                                        
  [0]: Debugprobe on Pico (CMSIS-DAP) -- 2e8a:000c-0:41E6E41C567D858D           
  (CMSIS-DAP)                                                                   

● That output is actually healthy — and I owe you a correction: I told you you'd
  see rx fps 0 ticking each second while idle. That was wrong. Look at the loop
  — after arming the DMA and raising READY, the firmware blocks in while 
  rx_busy(&rx_ch) waiting for the first byte, and the stall-watchdog
  deliberately doesn't fire on a not-yet-started transfer (it only catches a
  transfer that starts then stalls). So with nothing arriving, it waits forever 
  on frame 1 and never reaches the rx fps print. Banner-then-silence is exactly
  the "armed and waiting for the Pi" idle state — you'd see it with correct or
  incorrect wiring, as long as nothing is being sent.

  And nothing was being sent — you ran cargo run and watched, but never started
  the ramp. The firmware just sat there politely waiting. (Also good news: your
  probe is on the desktop — Debugprobe on Pico — so RTT works from here, and the
  banner proves the firmware booted, armed PIO1, and raised READY clean.)

  So the real test needs both running at once: keep cargo run attached on the
  desktop, and in a Pi terminal start the ramp. Let me add a heartbeat to the
  ramp so you get an unambiguous three-way readout of where any break is:

● Update(~/Projects/rayglow/tools/pio_ramp.py)  ⎿  Added 11 lines, removed 1 line
      40                   nibble_swap=not args.pio_no_nibble_swap)
      41      print(f"ramp: {len(frame)} bytes, clkdiv={args.pio_clkdiv:g
          }, "
      42            f"nibble_swap={not args.pio_no_nibble_swap}. Ctrl-C t
          o stop.")
      43 +    # Heartbeat: if this count climbs, the Pi is sending (got R
         +EADY, clocked a                                                
      44 +    # frame). If it stays 0, send() is blocked waiting on READY
         + — a control-line                                              
      45 +    # problem, not a data-lane one.                            
      46 +    sent = 0                                                   
      47 +    t0 = last = time.monotonic()                               
      48      try:
      49          while True:
      50              out.send(frame)
      46 -            time.sleep(0.05)                                   
      51 +            sent += 1                                          
      52 +            now = time.monotonic()                             
      53 +            if now - last >= 1.0:                              
      54 +                print(f"sent {sent} frames ({sent / (now - t0):
         +.0f}/s)")                                                      
      55 +                last = now                                     
      56 +            time.sleep(0.02)                                   
      57      except KeyboardInterrupt:
      58          pass
      59      finally:

  Ran 1 shell command (ctrl+o to expand)

● Compiles. Now run them together — desktop session attached, Pi sending — and
  the heartbeat will pinpoint any break:

  Desktop (re-attach RTT; your flashed firmware already has the byte-dump):
  cd firmware && cargo run --bin phase6-parallel
  Pi (separate terminal, local cwd):
  cd /tmp
  sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-clkdiv 16

  Then read the two terminals against this table:

  ┌────────────────┬─────────────┬───────────────────┬─────────────────────┐
  │   Pi sent N    │ Desktop rx  │                   │                     │
  │     frames     │   fps /     │     Diagnosis     │         Fix         │
  │                │  rx[0..8]   │                   │                     │
  ├────────────────┼─────────────┼───────────────────┼─────────────────────┤
  │                │ rx fps >0,  │ data flowing —    │ read nibble order   │
  │ climbs         │ rx[0..8]    │ paste the bytes   │ (table from last    │
  │                │ prints      │                   │ msg)                │
  ├────────────────┼─────────────┼───────────────────┼─────────────────────┤
  │                │ nothing     │ Pi sends but      │ check CS (Pi        │
  │ climbs         │ (still just │ RP2350 never      │ GPIO21→GP25) and    │
  │                │  the        │ advances → CS or  │ DCLK (Pi            │
  │                │ banner)     │ DCLK not arriving │ GPIO20→GP24)        │
  ├────────────────┼─────────────┼───────────────────┼─────────────────────┤
  │ stuck at 0     │             │ send() blocked on │ check READY (RP2350 │
  │ (heartbeat     │ nothing     │  READY → READY    │  GP26→Pi GPIO25);   │
  │ never prints)  │             │ not reaching the  │ confirm             │
  │                │             │ Pi                │ --ready-gpio 25     │
  └────────────────┴─────────────┴───────────────────┴─────────────────────┘

  That three-way split tells us exactly which signal class is wrong before we
  even look at data lanes:
  - READY is the handshake (RP2350→Pi). If it's dead, the Pi never even starts a
  transfer — heartbeat stays 0.
  - CS + DCLK are what make the RP2350 advance. If those are dead but READY
  works, the Pi happily clocks into the void — heartbeat climbs, firmware stays
  silent.
  - Only once both move do the data lanes matter, and then rx[0..8] decodes
  them.

  Given you flagged the rewire as suspect, my bet is row 2 or 3. Paste both
  terminals (the Pi heartbeat line + whatever the desktop shows) and I'll tell
  you which wire.

✻ Sautéed for 2m 5s

❯ Okay that was what I attempted the first time, but the rpi errors out. I did  
  the rebuild in sequence again so you could see the steps. From the rpi5:      
  will@raspberrypi ~/r/r/r/piobridge (main)> cd                                 
  ~/rayglow/.reference/rpi5/utils/piolib && cmake -DBUILD_SHARED_LIBS=1 . &&    
  make                                                                          
  CMake Warning (dev) at /usr/share/cmake-3.31/Modules/GNUInstallDirs.cmake:253 
  (message):                                                                    
    Unable to determine default CMAKE_INSTALL_LIBDIR directory because no       
    target architecture is known.  Please enable at least one language before   
    including GNUInstallDirs.                                                   
  Call Stack (most recent call first):                                          
    CMakeLists.txt:3 (include)                                                  
  This warning is for project developers.  Use -Wno-dev to suppress it.         
                                                                                
  -- Configuring done (0.1s)                                                    
  -- Generating done (0.3s)                                                     
  -- Build files have been written to:                                          
  /home/will/rayglow/.reference/rpi5/utils/piolib                               
  [ 20%] Built target pio                                                       
  [ 30%] Built target apitest                                                   
  [ 40%] Built target piotest                                                   
  [ 50%] Built target piopwm                                                    
  [ 60%] Built target pioseq                                                    
  [ 70%] Built target piows2812                                                 
  [ 80%] Built target dpi_csync                                                 
  [ 90%] Built target rp1sm                                                     
  [100%] Built target quadenc                                                   
  will@raspberrypi ~/r/.r/r/u/piolib (master)> cd                               
  ~/rayglow/rayglow/render/piobridge && make                                    
  make: 'libpioshim.so' is up to date.                                          
  will@raspberrypi ~/r/r/r/piobridge (main)> cd /tmp                            
                                             sudo ~/venv/bin/python             
  ~/rayglow/tools/pio_ramp.py --pio-clkdiv 16                                   
  Traceback (most recent call last):                                            
    File "/home/will/rayglow/tools/pio_ramp.py", line 64, in <module>           
      main()                                                                    
      ~~~~^^                                                                    
    File "/home/will/rayglow/tools/pio_ramp.py", line 39, in main               
      out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.ready_gpio,           
                   nibble_swap=not args.pio_no_nibble_swap)                     
    File "/home/will/rayglow/rayglow/render/pio_out.py", line 37, in __init__   
      self._lib = ctypes.CDLL(path)                                             
                  ~~~~~~~~~~~^^^^^^                                             
    File "/usr/lib/python3.13/ctypes/__init__.py", line 390, in __init__        
      self._handle = _dlopen(self._name, mode)                                  
                     ~~~~~~~^^^^^^^^^^^^^^^^^^                                  
  OSError: libpio.so.0: cannot open shared object file: No such file or         
  directory                                                                     
  will@raspberrypi /tmp [1]>                                                    

● That's a classic dynamic-linker miss, not a wiring issue — and an easy fix.
  The shim built fine, but at runtime the loader can't find libpio.so.0: you
  built piolib in place (cmake . + make) without installing it, so libpio.so.0
  lives in the piolib dir, which isn't on the loader's search path.
  libpioshim.so records that it needs libpio.so.0 but not where to find it.

  The clean, durable fix is to bake the location into the shim with an rpath so
  it finds libpio.so.0 relative to nothing but its own recorded path — no env
  vars, survives sudo (which scrubs LD_LIBRARY_PATH). Let me add that to the
  Makefile:

● Update(~/Projects/rayglow/rayglow/render/piobridge/Makefile)
  ⎿  Added 5 lines, removed 1 line
      13  CC     ?= cc
      14  CFLAGS ?= -O2 -Wall -fPIC -shared
      15  
      16 +# -Wl,-rpath bakes piolib's absolute location into libpioshim.s
         +o so the loader                                                
      17 +# finds libpio.so.0 at runtime (piolib is built in place, not i
         +nstalled; and sudo                                             
      18 +# scrubs LD_LIBRARY_PATH). Re-run `make clean && make` after mo
         +ving the repo.                                                 
      19  libpioshim.so: pio_shim.c
      17 -  $(CC) $(CFLAGS) -I$(PIOLIB)/include $< -L$(PIOLIB) -lpio -o $
         -@                                                              
      20 +  $(CC) $(CFLAGS) -I$(PIOLIB)/include $< \                     
      21 +    -L$(PIOLIB) -Wl,-rpath,$(abspath $(PIOLIB)) -lpio -o $@    
      22  
      23  clean:
      24    rm -f libpioshim.so

● Then rebuild the shim (it won't auto-rebuild — make said "up to date" because
  the Makefile change doesn't touch pio_shim.c, so force it):

  cd ~/rayglow/rayglow/render/piobridge
  make clean && make
  ldd libpioshim.so | grep pio   # should now show: libpio.so.0 =>
  /home/.../piolib/libpio.so.0  (not "not found")

  Then the ramp loads clean — re-run it (with cargo run still attached on the
  desktop):
  cd /tmp
  sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-clkdiv 16

  If you'd rather not rebuild just to test, there's a one-shot env workaround
  (sudo env passes the var through sudo's scrub):
  cd /tmp
  sudo env LD_LIBRARY_PATH=/home/will/rayglow/.reference/rpi5/utils/piolib
  ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-clkdiv 16
  …but the rpath rebuild is the durable fix — once it's baked in, plain sudo 
  python … just works.

  This was purely the loader not knowing where libpio.so.0 lives — your wiring
  hasn't even been exercised yet. Once the ramp runs, we're back to the
  three-way heartbeat table: Pi sent N frames climbing or stuck, and the
  desktop's rx[0..8]. Paste both and we'll finally see real bytes (or find the
  first dead control line).

✻ Baked for 1m 8s

※ recap: You're replacing the SPI link with a 4-lane PIO parallel bus to remove the throughput bottleneck. Next: rebuild the shim on the Pi (make clean && make), then run the ramp tool with cargo run attached and paste the rx[0..8] bytes.

❯ Okay actual feedback this time:                                               
                                                                                
  rpi5                                                                          
  will@raspberrypi /tmp> sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py     
  --pio-clkdiv 16                                                               
  pio_out: 4-lane PIO bus — DATA0=GPIO12, CLK=GPIO20, CS=GPIO21, READY=GPIO25,  
  clkdiv=16                                                                     
  ramp: 32768 bytes, clkdiv=16, nibble_swap=True. Ctrl-C to stop.               
  sent 34 frames (33/s)                                                         
  sent 67 frames (33/s)                                                         
  [...]                                                                         
                                                                                
  rp2350b                                                                       
  [INFO ] rx fps 34 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 33 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 33 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 33 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [...]                                                                         
                                                                                
  ---                                                                           
  Steady increase in fps as clkdiv lowered:                                     
                                                                                
  rpi                                                                           
  sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-clkdiv 3             
                                                                                
  rp2350                                                                        
  [INFO ] rx fps 46 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 46 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 46 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
                                                                                
  ---                                                                           
  then breaks at clkdiv 2                                                       
                                                                                
  rpi5                                                                          
  will@raspberrypi /tmp> sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py     
  --pio-clkdiv 16                                                               
                                                                                
  rp2350                                                                        
  [INFO ] rx fps 24 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x12 0x34 0x56 0x78 0x9a 0xbc 0xde (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 24 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x00 0x00 0x00 0x00 0x00 0x00 0x00 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 24 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x12 0x34 0x56 0x78 0x9a 0xbc 0xde (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 24 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x00 0x00 0x00 0x00 0x00 0x00 0x00 0x00 (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               
  [INFO ] rx fps 24 (drops 0) (phase6_parallel src/bin/phase6_parallel.rs:357)  
  [INFO ]   rx[0..8] = 0x34 0x00 0x12 0x34 0x56 0x78 0x9a 0xbc (phase6_parallel 
  src/bin/phase6_parallel.rs:374)                                               

● It works — and your data is textbook. Let me decode it:

  - clkdiv 16 and clkdiv 3: rx[0..8] = 00 01 02 03 04 05 06 07 — byte-perfect.
  That confirms three things at once: nibble_swap=True is correct (no
  --pio-no-nibble-swap needed), lane order is correct (no DATA swap needed), and
  the framebuffer is byte-identical to what the Pi sends. My derivation held.
  The link is real. 🎉
  - clkdiv 2: the bytes go non-deterministic — 00 12 34 56 … (the ramp slipped
  by one nibble) alternating with 00 00 00 … (missed samples) and 34 00 12 …
  (slipped a byte). That shifting, frame-to-frame-inconsistent pattern is the
  signature of signal integrity / sampling-window failure, not a logic bug (a
  logic bug would be consistent). At clkdiv 2 the data clock is 50 MHz;
  single-ended 50 MHz on flying-wire jumpers rings and skews enough that the
  RP2350 samples on the edge of the window. Not a code problem — a wiring-speed
  problem.

  On the fps numbers: those are capped by the ramp tool's own time.sleep(0.02)
  (~50/s ceiling), not the link. clkdiv 16 → ~10 ms transfer + 20 ms sleep ≈ 33
  fps; clkdiv 3 → ~2 ms transfer + 20 ms sleep ≈ 46 fps. The link itself at
  clkdiv 3 is ~2 ms/frame (133 Mbit/s over 4 lanes) — vs 6.6 ms over 40 MHz SPI.
  The real renderer will show the true throughput.

  Your validated operating point is clkdiv 3 (33 MHz clock, ~2 ms/frame, drops
  0). clkdiv 2 wants better SI — shorter lanes, a ground return beside the
  bundle, or just wait for the PCB; clkdiv 3 already lifts the link off the
  critical path, which is the whole goal.

  Validation's done, so let me retire the byte-dump debug:

● Update(src/bin/phase6_parallel.rs)
  ⎿  Added 3 lines, removed 2 lines
      290      let mut last_us: u32 = timer.get_counter_low();
      291      // Bring-up aid: address of the most recently received fra
           me, so the per-second
      292      // telemetry can dump its first bytes for the nibble/lane-
           order check against a
      293 -    // known ramp from `tools/pio_ramp.py`. Set false once the
          - link is trusted.                                             
      294 -    const RX_DEBUG_BYTES: bool = true;                        
      293 +    // known ramp from `tools/pio_ramp.py`. Validated 2026-06 
          +(nibble_swap=True,                                            
      294 +    // lanes correct, clean ≤ clkdiv 3); flip true to re-check
          + after a rewire.                                              
      295 +    const RX_DEBUG_BYTES: bool = false;                       
      296      let mut last_good: u32 = 0;
      297      let mut sm = rx_sm.start();
      298  

❯ typo: I typed that wrong, last command is --pio-clkdiv 2 

● Noted — and that typo confirms the read: the broken run was --pio-clkdiv 2,
  exactly the 50 MHz point I pinned as the SI wall. Nothing changes; clkdiv 3 is
  your clean ceiling on this harness.

  Let me confirm the debug-off firmware still builds, then you're clear to run
  the real thing:

  Ran 1 shell command (ctrl+o to expand)

● Clean build. Let me record that the link is now hardware-validated (the memory
  still says "not yet"):

  Recalled 1 memory, wrote 2 memories (ctrl+o to expand)

● Recorded. Now the payoff run — reflash (picks up the debug-off build) and
  drive the real renderer over the parallel link:

  Desktop:
  cd firmware && cargo run --bin phase6-parallel
  Pi:
  cd /tmp
  sudo ~/venv/bin/python -m rayglow.render
  ~/rayglow/rayglow/render/presets/will-circles.glsl \
      --transport pio --pio-clkdiv 3 --fps 240

  What to watch in the telemetry line (it'll now read PIO floor … @ clkdiv 3):
  - send should drop from ~7.9 ms (SPI) to ~2 ms — the parallel transfer time.
  - wait should sit near 0 on anything but the very lightest shaders — the link
  is now so far under render that the _SendPipe overlap hides it trivially.
  - For your lightest shaders (render ~6 ms), the uncapped ceiling rises from
  ~126 fps (capped by the old 7.9 ms link) toward ~165 fps — they're now purely
  render-bound. At your 120 cap, everything with render < ~8 ms pins 120 with
  margin.

  That's Workstream 3 complete and validated end-to-end — the transport you
  built from RP1 PIO to RP2350 PIO is byte-perfect and ~3–4× the SPI link,
  entirely within the "open, you-built-it" world.

  A couple of optional follow-ups when you're ready (no rush):
  - Pixel-identical A/B: run the same shader under --transport spi then
  --transport pio and confirm the panel is identical — the ramp already proved
  byte-identity at the framebuffer, so this is just the satisfying visual
  confirmation.
  - Push past clkdiv 3 later with shorter, ground-returned lanes (or the PCB) —
  clkdiv 2 → 50 MHz → ~1 ms/frame if the SI holds.
  - Two-chain swap when the PCB lands: the three marked SINGLE-CHAIN lines in
  phase6_parallel.rs + pack() instead of pack_single(), and you're driving the
  full wall over the parallel bus.

  Paste the renderer's telemetry line when you run it — I'm curious to see send
  and wait on a heavy shader like neon_tunnel now that the link is basically
  free.

✻ Baked for 2m 37s

※ recap: The goal is faster audio-reactive shaders on the LED wall by replacing the SPI link with a 4-lane parallel PIO bus. That link is now validated byte-perfect at clkdiv 3. Next: run the real renderer with --transport pio --pio-clkdiv 3 and check the telemetry.

❯                                                                               
  Took a sec to troubleshoot my own wiring. Apparently I missed a shared ground 
  at some point and grounding them all together changed the display from        
  complete noise to pixel perfect. It looks great and runs great. One note: the 
  spi flag shows the data transfering but *nothing displays*. The display       
  holds the last frame shown when ran under the pio flag. This is honestly fine 
  considering how well it is running - I doubt I will return to SPI. That said  
  the numbers below are a little misleading because when I pushed spi-hz past   
  40MHz the visual seemed to break completely. This is running the PIO at       
  clkdiv 3 without issue. The SPI is shown at 48MHz which, at least according   
  to my previous test, wouldn't even render. Kudos on the rework:               
                                                                                
  will@raspberrypi ~> sudo ~/venv/bin/python -m rayglow.render                  
  ~/rayglow/rayglow/render/presets/will-helix.glsl \                            
                              --transport pio --pio-clkdiv 3 --fps 240          
  GPU: V3D 7.1.7.0 | OpenGL ES 3.1 Mesa 25.0.7-2+rpt4 | GLSL OpenGL ES GLSL ES  
  3.10                                                                          
  image iChannel0 <- milk (milk)                                                
  audio: listening on UDP                                                       
  pio_out: 4-lane PIO bus — DATA0=GPIO12, CLK=GPIO20, CS=GPIO21, READY=GPIO25,  
  clkdiv=3                                                                      
   145.3 fps | render   6.0ms  pack  0.7ms  send   2.1ms  wait   0.0ms (PIO     
  floor  1.0ms @ clkdiv 3, 32KB)                                                
   145.5 fps | render   6.0ms  pack  0.6ms  send   2.1ms  wait   0.0ms (PIO     
  floor  1.0ms @ clkdiv 3, 32KB)                                                
                                                                                
  will@raspberrypi ~> sudo ~/venv/bin/python -m rayglow.render                  
  ~/rayglow/rayglow/render/presets/will-helix.glsl \                            
                              --transport spi --spi-hz 48000000 --fps 240       
  GPU: V3D 7.1.7.0 | OpenGL ES 3.1 Mesa 25.0.7-2+rpt4 | GLSL OpenGL ES GLSL ES  
  3.10                                                                          
  image iChannel0 <- milk (milk)                                                
  audio: listening on UDP                                                       
  spi_out: SPI0.0 @ 48.00 MHz mode 0, READY=GPIO25                              
   126.2 fps | render   6.0ms  pack  0.6ms  send   7.9ms  wait   1.2ms (SPI     
  floor  5.5ms @ 48MHz, 32KB)                                                   
   125.9 fps | render   6.0ms  pack  0.6ms  send   7.9ms  wait   1.2ms (SPI     
  floor  5.5ms @ 48MHz, 32KB)                                                   

● Congratulations — that's the whole thing working, and the ground fix is the
  perfect capstone diagnosis. "Complete noise → pixel-perfect the instant I tied
  the grounds together" is the textbook signature of a missing return path:
  without a shared ground, your 4 data lanes + clock have no clean reference to
  swing against, so the RP2350 samples garbage. That's exactly why clkdiv 2 fell
  apart earlier — same root cause, just more sensitive at 50 MHz. You found it
  the right way.

  The SPI "mystery" — it's not displaying because there's nothing to display to

  This one's expected, not a bug. phase6-parallel has no SPI receiver — its PIO1
  only speaks the 4-lane parallel protocol (CS on GP25, DCLK on GP24, in pins,4
  from GP20), and your wiring is parallel now (GP20–23 are data lanes, not the
  old SPI SCLK/MOSI/CS). So under --transport spi:
  - The Pi's SpiOut waits on READY — and gets it, because the firmware does
  raise READY (GP26) while armed for a parallel frame.
  - It then clocks 32 KB of SPI out GPIO 8/10/11 into pins the RP2350 isn't
  reading as SPI. The bytes go into the void; the parallel RX never sees a
  CS/DCLK sequence, so the display holds the last good (PIO) frame.

  So your instinct is dead right: those SPI numbers are meaningless. "48 MHz
  works now when 40 broke before" isn't SI improving — it's that nothing is 
  receiving, so signal integrity is irrelevant; the Pi is talking to itself. The
  honest SPI baseline is your earlier displaying runs (~126 fps, clamped by the
  real 7.9 ms link). To actually use SPI you'd reflash phase-experimental and
  move the wires back — which you won't, and shouldn't.

  The real result

  PIO clkdiv 3:  145 fps | render 6.0  pack 0.7  send 2.1  wait 0.0   ← link
  fully hidden
  real SPI base: 126 fps | render 6.0  pack 0.6  send 7.9  wait 1.2   ← link
  leaking into the path
  send 7.9 → 2.1 ms, wait → 0. will-helix is now purely render-bound (6.7 ms
  render+pack → ~145 fps); the transport is no longer the constraint at any
  setting. The 2.1 ms (vs the 1.0 ms PIO floor) is just the fixed per-frame
  overhead — gpiozero CS framing + the RP1 ioctl round-trip — and since wait is
  0, it costs you nothing. The link is genuinely done.

  Optional, no real upside: now that the grounds are solid you could retry
  clkdiv 2 (~1 ms floor) — but wait is already 0, so there's no frame-rate to
  reclaim. The link has done its job; pushing it further is curiosity, not need.

  Where you landed

  The full arc this session: 24 MHz SPI slideshow → vectorized pack +
  render∥send overlap (link-bound → render-bound) → 4-lane RP1-PIO ↔ RP2350-PIO 
  bus (byte-perfect, ~3.5× the SPI throughput, off the critical path). Every
  layer is one you built and can inspect with a scope and a datasheet — PIO to
  PIO, no black box. That's Workstream 3 complete and hardware-validated.

  When you're ready, this is the natural point to land it: merge
  feat/pio-parallel-link → main and tag v0.2.0 (the reward, now that it works).
  And the two-chain swap is waiting for the PCB — three marked lines in
  phase6_parallel.rs plus pack() and the whole wall rides the parallel bus.

  Genuinely great work on the bench tonight — the kind of bug (a missing ground)
  that humbles everyone, found and fixed by reasoning about return paths. Enjoy
  watching it run. ✌️

✻ Cogitated for 1m 43s