void mainImage( out vec4 O, in vec2 C ) {
    vec2 p = (C-.5*iResolution.xy)/iResolution.y;
    float t=iTime, z=1./(p.y+1.5), w=sin(p.x*4.+t)*.2;
    vec2 v=p*z;
    float g=abs(sin(v.x*10.+t)*sin(v.y*10.-t*2.+w*10.));
    vec3 c=mix(vec3(.1,0,.2),vec3(0,.8,1),p.y+.5);
    O=vec4(c + (0.01/(g+.02))*mix(vec3(1,0,1),vec3(0,1,1),sin(t)*.5+.5),1)*smoothstep(1.5,0.2,p.y);
}
