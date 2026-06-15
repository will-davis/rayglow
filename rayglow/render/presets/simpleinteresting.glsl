void mainImage( out vec4 O, vec2 I ){
    // Iterator, distance travelled, density
    float i, t, v;
    // Raymarching loop
    for (O*=i; i++<50.;){
    vec3 p=t*normalize(vec3(I+I,1)-iResolution.xyy);
    // Rotation of xy coordinates based on distance travelled
    p.xy*=mat2(cos(t*.15+vec4(0,11,33,0)));
    // Move forward
    p.z-=iTime;
    // Coordinate repetition
    p=mod(p,4.)-2.;
    // Density from mix between sphere and line sdf based on distance travelled
    v = mix(abs(length(p)-1.),length(p.xz),.5-.5*cos(t))+.01;
    // Travel forwards based on density
    t+=v*.3;
    // Color accumulation based on density and distance travelled
    O+=exp(sin(t+vec4(0,2,4,0)))/v;
    }
    // Tone mapping
    O = tanh(O/2e2);
}
