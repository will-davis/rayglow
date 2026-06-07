// Image pass: display Buffer A's Game of Life state.
// Multipass demo — life.bufA.glsl runs the simulation; this pass only
// colors it.  Young cells green, long-stable cells fade to ember red.
// Run at --scale 1 for chunky cells or higher for a finer colony.
// iChannel0: bufA

void mainImage(out vec4 o, in vec2 u) {
    vec2 uv = u / iResolution.xy;
    vec4 s = texture(iChannel0, uv);
    vec3 col = s.x * mix(vec3(0.2, 1.0, 0.4), vec3(1.0, 0.35, 0.1),
                         clamp(s.y / 40.0, 0.0, 1.0));
    o = vec4(col, 1.0);
}
