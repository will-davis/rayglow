void mainImage( out vec4 O, vec2 C ) {
    vec2 p = (C * 2. - iResolution.xy) / iResolution.y;
    float t = iTime * .2;
    vec3 col = vec3(0);
    
    for(float i=0.; i<8.; i++) {
        p = abs(p) - .5;
        p *= mat2(cos(t), -sin(t), sin(t), cos(t)); 
        p = abs(p) - .2;
        p *= 1.5;
        // Color based on iteration and distance
        col += vec3(0.1, 0.2, 0.3) / length(p) * (0.5 + 0.5 * cos(t + i + vec3(0,2,4)));
    }
    
    O = vec4(pow(col, vec3(.4545)) , 1); // Gamma correction for pop
}
