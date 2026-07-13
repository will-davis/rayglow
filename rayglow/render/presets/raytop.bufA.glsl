// raytop.bufA.glsl — the scrolling memory of the system monitor.
//
// raytop is a truncated btop on the 256x64 wall, fed by raytop_sender.py (run it
// on the desktop, point --host at the Pi). This pass owns the *time* axis: a
// horizontal waterfall/graph history that streams LEFT. Each step it writes the
// newest sample into the RIGHT column and shifts every older column left one
// pixel — the GPU does the scroll for free; the desktop only lands UDP packets,
// and the sender only ever sends the *current* scalar (no time series on the wire).
//
// The current sample arrives on the milk texture as the Pi-derived ~125ms
// envelope (.w) of the two band slots the telemetry sender fills:
//   texel0 .w = primary fraction  (GPU util / CPU util / net-down) -> stored in .r
//   texel1 .w = secondary fraction (VRAM / RAM / net-up frac)       -> stored in .g
// Using .w (env) rather than .x (imm) gives an already-smoothed line. The whole
// column gets the same scalar; the mirror about the centerline happens in the
// image pass, so this stays a clean 1-D history that can be restyled freely.
//
// iChannel0 (self) = this buffer's previous frame, the history we scroll.
// iChannel1 (milk) = the telemetry scalars (texelFetch, 13x1 RGBA32F).
// (Directive lines below must be bare specs — the parser reads the whole line.)
// iChannel0: self
// iChannel1: milk

// Frames between scroll steps. At --fps 30 / --scale 4 the buffer is 1024 wide;
// one new sub-pixel column per frame = 30 cols/s = ~7.5 panel-px/s, so the ~85px
// waterfall shows ~11 s of history. Raise to slow the scroll / show more time.
#define SCROLL_DIV 1

void mainImage(out vec4 O, in vec2 I) {
    vec2 R = iResolution.xy;
    ivec2 p = ivec2(I);
    int right = int(R.x) - 1;               // newest column lives at the right edge

    // Hold between scroll steps: copy our own previous frame unchanged.
    if ((iFrame % SCROLL_DIV) != 0) {
        O = texelFetch(iChannel0, p, 0);
        return;
    }

    if (p.x == right) {
        // Newest sample enters at the right, filling the whole column uniformly.
        float primary   = texelFetch(iChannel1, ivec2(0, 0), 0).w;   // .w = smoothed env
        float secondary = texelFetch(iChannel1, ivec2(1, 0), 0).w;
        O = vec4(primary, secondary, 0.0, 1.0);
    } else {
        // Everyone else inherits the column to their right — history slides left.
        O = texelFetch(iChannel0, ivec2(p.x + 1, p.y), 0);
    }
}
