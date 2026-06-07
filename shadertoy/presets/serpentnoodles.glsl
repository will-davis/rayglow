// CC0: Another hex truchet
//  Saw an image of a truchet pattern that look pretty sweet.
//  Even better it was not hard to recreate

// I have been trying to make it small as well but I ran out of
//  inspiration so this is as good as it gets for now.

// Twigl: https://twigl.app?ol=true&ss=-Ou2oMpqmMtSVJfGQuOW

// If somone is curious how it looked before minification started:
//  https://www.shadertoy.com/view/f323Dc

// This file is released under CC0 1.0 Universal (Public Domain Dedication).
// To the extent possible under law, mrange has waived all copyright
// and related or neighboring rights to this work.
// See <https://creativecommons.org/publicdomain/zero/1.0/> for details.

#define L length// This is a silly way to save 1 char
#define M(D) d=abs(D) - .05, o -= o/exp(30.*d+.4), o += (1.-o) * S d);//
#define S smoothstep(Z,-Z,

void mainImage(out vec4 o, vec2 C) {
  float
    d
  , Z = 4./iResolution.y
  ;
  vec4
    U = vec4(3,2,1,0)*.289 // U are constants involving sqrt(3). for hexagon
  ;
  o *= d;
  vec2
    k = vec2(.5,U)
  , a,b
  ;
  a = L( a = mod( C= C*Z + 6.*sin(iTime*k/9.), k+k ) - k )
    < L( b = mod( C - k                      , k+k ) - k )
      ? a : b;
  C = a * mat2(cos( 1.571*( ceil(6.*sin(dot(Z+C-a,vec2(365,511))))/1.5 + vec4(0,1,-1,0))));
  k.x = -.5;
  o.r = S max(0., dot( a= abs(C), k+k ))*k - a  + .49 ).x;
  M(min(
        L( C - U.wy) - U.z
      , L( C + k   ) - U.x
      ))
  k.y = -k.y;
  M(L( C - k ) - U.x)
}

