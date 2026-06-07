// Buffer A: Conway's Game of Life, simulated in a float buffer.
// The directive below is this pipeline's stand-in for Shadertoy's channel
// config UI: bind this buffer's own previous frame to iChannel0.
// iChannel0: self

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

float cell(ivec2 p, ivec2 size) {
    p = (p + size) % size;                  // wrap-around world
    return texelFetch(iChannel0, p, 0).x > 0.5 ? 1.0 : 0.0;
}

void mainImage(out vec4 o, in vec2 u) {
    ivec2 size = ivec2(iResolution.xy);
    ivec2 p = ivec2(u);
    if (iFrame == 0) {                      // seed once from a hash
        o = vec4(step(0.68, hash(u)), 0.0, 0.0, 1.0);
        return;
    }
    float n = 0.0;
    for (int dy = -1; dy <= 1; dy++)
    for (int dx = -1; dx <= 1; dx++)
        if (dx != 0 || dy != 0) n += cell(p + ivec2(dx, dy), size);

    float alive = cell(p, size);
    float next = (alive > 0.5) ? ((n == 2.0 || n == 3.0) ? 1.0 : 0.0)
                               : ((n == 3.0) ? 1.0 : 0.0);
    // .y accumulates cell age — only representable thanks to the float
    // buffer; the image pass colors by it.
    float age = (next > 0.5 && alive > 0.5)
        ? texelFetch(iChannel0, p, 0).y + 1.0 : 0.0;
    o = vec4(next, age, 0.0, 1.0);
}
