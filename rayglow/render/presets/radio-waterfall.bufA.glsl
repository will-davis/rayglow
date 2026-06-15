// radio-waterfall.bufA.glsl — the scrolling memory of the waterfall.
//
// A classic RF waterfall is frequency on X, time on Y, magnitude as color.
// This pass owns the *time* axis: each step it writes the newest spectrum into
// the top row and shifts every older row down one pixel, so history streams
// downward off the bottom.  The GPU does the scroll for free (one texture read
// per pixel) — the Pi's CPU only has to land the UDP packet.
//
// The spectrum arrives on the audio texture's row y=0.75: the SDR sender
// (sdr-pi-feed) packs its 128-bin dB spectrum into the milk packet's wave[],
// and the Pi reconstructs it there as 0.0 = floor dB .. 1.0 = ceil dB.  We
// store that scalar magnitude; the image pass turns it into color.
//
// iChannel0 (self)  = this buffer's previous frame, the history we scroll.
// iChannel1 (audio) = row y=0.75 .x is the 0..1 RF spectrum across the band.
// (Directive lines below must be bare specs — the parser reads the whole line.)
// iChannel0: self
// iChannel1: audio

// How many frames between scroll steps.  The renderer runs ~60 fps but a full
// hackrf sweep completes only a few times a second, so scrolling every frame
// just stamps duplicate rows.  Higher = slower scroll = more time visible at
// once.  2 -> ~30 rows/s (≈4 s of history at the default --scale 4).
#define SCROLL_DIV 8

void mainImage(out vec4 O, in vec2 I) {
    vec2 R = iResolution.xy;
    ivec2 p = ivec2(I);
    int top = int(R.y) - 1;                 // gl_FragCoord.y grows upward: top row

    // Hold between scroll steps: copy our own previous frame unchanged.
    if ((iFrame % SCROLL_DIV) != 0) {
        O = texelFetch(iChannel0, p, 0);
        return;
    }

    if (p.y == top) {
        // Newest spectrum enters at the top.  Normalised x spans the band.
        float m = texture(iChannel1, vec2(I.x / R.x, 0.75)).x;
        O = vec4(m, 0.0, 0.0, 1.0);
    } else {
        // Everyone else inherits the row above them — history slides down.
        O = texelFetch(iChannel0, ivec2(p.x, p.y + 1), 0);
    }
}
