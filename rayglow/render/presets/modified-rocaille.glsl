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
    // Initialize output to zero to prevent NaN
    vec4 bass  = texelFetch(iChannel0, ivec2(0, 0), 0);
    vec4 mid   = texelFetch(iChannel0, ivec2(1, 0), 0);
    vec4 treb  = texelFetch(iChannel0, ivec2(2, 0), 0);
    vec4 vol   = texelFetch(iChannel0, ivec2(3, 0), 0);
    vec4 sub   = texelFetch(iChannel0, ivec2(4, 0), 0);
    vec4 theta = texelFetch(iChannel0, ivec2(5, 0), 0);
    vec4 meta  = texelFetch(iChannel0, ivec2(6, 0), 0);
    
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
