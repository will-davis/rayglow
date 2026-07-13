/*
"Rocaille" by @XorDev

This time I added multiple layers of turbulence
with time and color offsets. Loved the shapes.

-1 Thanks to GregRostami
*/
// void mainImage(out vec4 O, vec2 I)
// {
// //Vector for scaling and turbulence
// vec2 v = iResolution.xy,
// //Centered and scaled coordinates
// p = (I+I-v)/v.y/.3;
// 
// //Iterators for layers and turbulence frequency
// float i, f;
// for(O*=i;i++<9.;
//     //Add coloring, attenuating with turbulent coordinates
//     O += (cos(i+vec4(0,1,2,3))+1.)/6./length(v))
//     //Turbulence loop
//     //https://mini.gmshaders.com/p/turbulence
//     for(v=p,f=0.;f++<9.;v+=sin(v.yx*f+i+iTime)/f);
// 
// //Tanh tonemapping
// //https://www.shadertoy.com/view/ms3BD7
// O = tanh(O*O);
// }

// iChannel0: milk

void mainImage(out vec4 O, vec2 I)
{
    // Legacy scalars rebuilt on the v3 16x3 milk texture (see textures.py).
    // Old 13x1 texels 0-4 were (.x imm .y att .z ddt .w env): imm/att now live
    // in the legacy block (9..11,2); env = env0 (row 0 .y) of the nearest v3
    // band; ddt is gone in v3 (row 1 .w onset is its + half) -> 0.
    vec4 lvl   = texelFetch(iChannel0, ivec2(9, 2), 0);   // bass mid treb vol imm
    vec4 attv  = texelFetch(iChannel0, ivec2(10, 2), 0);  // atts + sub imm
    vec4 bass  = vec4(lvl.x, attv.x, 0.0, texelFetch(iChannel0, ivec2(2, 0), 0).y);
    vec4 mid   = vec4(lvl.y, attv.y, 0.0, texelFetch(iChannel0, ivec2(5, 0), 0).y);
    vec4 treb  = vec4(lvl.z, attv.z, 0.0, texelFetch(iChannel0, ivec2(7, 0), 0).y);
    vec4 vol   = vec4(lvl.w, lvl.w,  0.0, texelFetch(iChannel0, ivec2(8, 0), 0).y);
    vec4 sub   = vec4(attv.w, texelFetch(iChannel0, ivec2(11, 2), 0).x, 0.0,
                      texelFetch(iChannel0, ivec2(0, 0), 0).y);
    // old texel 5 (bass/mid/treb/vol theta) -> theta0 of the nearest v3 band
    vec4 theta = vec4(texelFetch(iChannel0, ivec2(2, 1), 0).x,
                      texelFetch(iChannel0, ivec2(5, 1), 0).x,
                      texelFetch(iChannel0, ivec2(7, 1), 0).x,
                      texelFetch(iChannel0, ivec2(8, 1), 0).x);
    // old texel 6 (.x sub theta, .yzw pkt_age/live/source_domain)
    vec4 meta  = vec4(texelFetch(iChannel0, ivec2(0, 1), 0).x,
                      texelFetch(iChannel0, ivec2(7, 2), 0).xyz);
    
    // Initialize output to zero to prevent NaN
    O = vec4(0.0);

    vec2 v = iResolution.xy;
    // Centered and scaled coordinates
    vec2 p = (I + I - v) / v.y / 2.2;

    float i = 0.0; // Explicitly initialize iterator
    float f = 0.0;

    for(i = 0.0; i < 18.0; i++)
    {
        // Reset v to p for the turbulence calculation each layer
        vec2 tv = p; 
        f = 0.0;
        
        for(f = 0.0; f < 24.0; f++)
        {
            // Turbulence loop
            tv += sin(tv.yx * f + i + iTime + meta.x) / (f + 1.0); // Added +1 to avoid div by 0
            f++;
        }
        
        // Add coloring, attenuating with turbulent coordinates
        // Note: using length(tv) instead of length(v) to make the 
        // visual actually react to the turbulence calculated above.
        O += (cos(i + vec4(0, 1, 2, 1)) + 0.6) / 8.0 / length(tv);
    }

    // Tanh tonemapping
    O = tanh(O * O);
    // less bright
    O.rgb = pow(O.rgb, vec3(1.0 / 0.05));
}
