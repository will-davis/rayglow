
const vec2 PANEL = vec2(256.0, 64.0);

vec3 palette(float t)
{
    vec3 a = vec3(0.5, 0.5, 0.5);
    vec3 b = vec3(0.5, 0.5, 0.5);
    vec3 c = vec3(0.5, 0.5, 0.5);
    vec3 d = vec3(0.0, 0.12, 0.24);
    return a + b * cos(6.28318 * (c * t + d));
}

float onepx = 0.5 / PANEL.x;
float onepy = 0.5 / PANEL.y;

vec2 pixellocation(vec2 pos)
{
    return vec2(pos.x * onepx, pos.y * onepy); 
}


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 coords = pixellocation(vec2(5.0, 5.0));
    vec2 p = fragCoord / iResolution.xy;
    vec3 col = vec3(0.0);
    float t = sin(iTime * 0.52) / 2.0 + 0.5;
    col = mix(col, vec3(1.0, 1.0, 1.0), step(abs(p.x - t), onepx));
    //col = mix(col, vec3(1.0, 1.0, 1.0), step(abs(p.y - t), onepy));

    fragColor = vec4(col,1.0);
}
