// ARCTAN EXCERCISE
float pi = 3.14159; //I'M MORE OF A TAU MAN MYSELF BUT WHO DOESN'T LIKE PI
float N = 1.0; // NUMBER OF SLICES BUT OF PIE NOT PI WAIT IS THIS WHY THEY CAL
float speed = 1.0;

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
// ##################### SCENE SETUP ###########################################
    vec2 p = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    float dist = length(p);
    float angle = atan(p.y, p,x);

// ##################### ARCTAN WRAP ###########################################
    float Nsteps = floor(mod(iTime * speed, N ));
    float field = fract(atan(p.y, p.x) / (2.0 * pi) * N);
    vec3 colB = vec3(field);

// ##################### RENDER ################################################
    fragColor = vec4(colB, 1.0);
}
