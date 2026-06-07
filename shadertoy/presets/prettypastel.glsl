/*
 * Try And Untie by NR4
 *
 * Copyright (C) 2026 Alexander Kraus <nr4@z10.info>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 */
int iSampleCount = 2;

vec2 iFormulaOrigin = vec2(-5.646, -16.745);
vec4 iFormulaNumerator = vec4(-322.05, 524.26, 21, 0.23);
vec4 iFormulaDenominator = vec4(-2222, -2.62, -5.99, -0.186);
float iFormulaExponentialAmount = 0.;
vec4 iFormulaExponentialArg = vec4(0);

vec2 iTrapOrigin = vec2(17.9, -2.64);
vec4 iTrapNumerator = vec4(-22.69, -326.79, 23.48, 1.84);
vec4 iTrapDenominator = vec4(-5.41, -33.57, 25.31, 2.72);
float iTrapExponentialAmount = 0.000016;
vec4 iTrapExponentialArg = vec4(-1.72, 0.085, -0.00052, -0.0000055);

vec4 iTrapNumerator2 = vec4(-422, -62.82, 2.49, 0.09);
vec4 iTrapDenominator2 = vec4(6.77, 4.411, 0.601, -0.07);
float iTrapExponentialAmount2 = 0.000004;
vec4 iTrapExponentialArg2 = vec4(-10.11, -0.129, 0.00025, 0.00005);

vec2 iCMAPCurvature = vec2(0.18, -0.14);
float iCMAPSplit = 0.49;
float iCMAPOffset = 2.5;
float iCMAPScale = 4.52;

float iCoordinateScale = 1.37;
vec2 iCoordinateOffset = vec2(-20, 28.23);
int iJacobiRepeats = 2;
int iIterationCount = 40;

const vec3 c = vec3(1,0,-1);
const float pi = 3.141592653589793;

// Created by David Hoskins and licensed under MIT.
// See https://www.shadertoy.com/view/4djSRW.
// vec2->float hash function
float hash12(vec2 p)
{
    vec3 p3  = fract(vec3(p.xyx) * .1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
// End of David Hoskins' MIT licensed code

// Low-Frequency noise (value-type)
float lfnoise(vec2 t)
{
    vec2 i = floor(t);
    t = fract(t);
    t = smoothstep(c.yy, c.xx, t);
    vec2 v1 = vec2(hash12(i), hash12(i+c.xy)),
        v2 = vec2(hash12(i+c.yx), hash12(i+c.xx));
    v1 = c.zz+2.*mix(v1, v2, t.y);
    return mix(v1.x, v1.y, t.x);
}

// Created with ImageColorPicker (https://github.com/LeStahL/ImageColorPicker).
vec3 c1(float t) {
    return vec3(0.20,0.11,0.09)
        +t*(vec3(2.41,3.97,1.95)
        +t*(vec3(-20.77,-44.43,16.57)
        +t*(vec3(153.33,183.16,-125.96)
        +t*(vec3(-401.40,-311.48,297.60)
        +t*(vec3(418.13,231.50,-293.64)
        +t*(vec3(-151.76,-62.60,103.55)
    ))))));
}

vec3 cmap(float t) {
    vec2 uv = (gl_FragCoord.xy - .5 * iResolution.xy) / iResolution.y;
    return mix(
        c1(t),
        c1(fract(1.1-t)),
        smoothstep(0., 1., .5 + .5 * lfnoise(uv + length(uv)+ .2 * iTime))
    );
}

vec2 cis(float a) {
    a = mod(a, 2. * pi);
    return vec2(cos(a), sin(a));
}

vec2 cmul(vec2 a, vec2 b) {
    return mat2(a, -a.y, a.x) * b;
}

vec2 cdiv(vec2 a, vec2 b) {
    return cmul(a, vec2(b.x, -b.y)) / max(dot(b, b), 1.e-4);
}

vec2 cexp(vec2 x) {
    return min(exp(x.x), 2.e4) * cis(x.y);
}

vec2 clog(vec2 a) {
    return vec2(log(length(a)),atan(a.y,a.x));
}

float piecewise_log(float x) {
    float split = clamp(iCMAPSplit, 0.001, 0.999);
    return fract(iCMAPOffset - (
        (x < split)
            ? (split * log(1.0 + iCMAPCurvature.x * x / split) / log(1.0 + iCMAPCurvature.x))
            : (split + (1.0 - split) * log(1.0 + iCMAPCurvature.y * (x - split) / (1.0 - split)) / log(1.0 + iCMAPCurvature.y))
    ));
}

vec2 ratexp(vec2 z, vec2 origin, vec4 numerator, vec4 denominator, float exp_amount, vec4 exp_arg) {
    vec2 z1 = z - origin;
    vec2 z2 = cmul(z1, z1);
    vec2 z3 = cmul(z2, z1);
    vec2 z4 = cmul(z2, z2);

    return cdiv(
        numerator.x * z1 + numerator.y * z2 + numerator.z * z3 + numerator.w * z4,
        denominator.x * z1 + denominator.y * z2 + denominator.z * z3 + denominator.w * z4
    ) + exp_amount * cexp(
        exp_arg.x * z1 + exp_arg.y * z2 + exp_arg.z * z3 + exp_arg.w * z4
    );
}

vec2 formula(vec2 z) {
    return ratexp(
        z,
        iFormulaOrigin,
        iFormulaNumerator,
        iFormulaDenominator,
        iFormulaExponentialAmount,
        iFormulaExponentialArg
    );
}

float trap(vec2 z) {
    return length(ratexp(
        z,
        iTrapOrigin,
        iTrapNumerator,
        iTrapDenominator,
        iTrapExponentialAmount,
        iTrapExponentialArg
    )) / length(ratexp(
        z,
        iTrapOrigin,
        iTrapNumerator2,
        iTrapDenominator2,
        iTrapExponentialAmount2,
        iTrapExponentialArg2
    ));
}

vec4 fractal(vec2 fragCoord)
{
    vec2 z = exp(mix(log(1.e-4), log(1.), iCoordinateScale))
        * (fragCoord - 0.5 * iResolution.xy) / iResolution.y - iCoordinateOffset;
    float tm = 1e9;
    for(int i = 0; i < iIterationCount && dot(z,z) < 1e10; ++i) {
        z = formula(z);
        tm = min(tm, iCMAPScale * trap(z));
    }
    return vec4(
        cmap(piecewise_log(fract(tm))),
        1
    );
}

// By mla, https://www.shadertoy.com/view/4tlBRl, CC-NC-BY-SA 4.0 unported
// Taken from Numerical Recipes, simplified by using a fixed number
// of iterations and removing negative modulus case.
// Modulus is passed in as k^2 (not 1-k^2 as in NR).
void sncndn(float u, float k2,
            out float sn, out float cn, out float dn) {
  float emc = 1.0-k2;
  float a,b,c;
  const int N = 4;
  float em[N],en[N];
  a = 1.0;
  dn = 1.0;
  for (int i = 0; i < N; i++) {
    em[i] = a;
    emc = sqrt(emc);
    en[i] = emc;
    c = 0.5*(a+emc);
    emc = a*emc;
    a = c;
  }
  // Nothing up to here depends on u, so
  // could be precalculated.
  u = c*u; sn = sin(u); cn = cos(u);
  if (sn != 0.0) {
    a = cn/sn; c = a*c;
    for(int i = N-1; i >= 0; i--) {
      b = em[i];
      a = c*a;
      c = dn*c;
      dn = (en[i]+a)/(b+a);
      a = c/b;
    }
    a = 1.0/sqrt(c*c + 1.0);
    if (sn < 0.0) sn = -a;
    else sn = a;
    cn = c*sn;
  }
}

// We don't use cn and dn, but just for reference:
vec2 cn(vec2 z, float k2) {
  float snu,cnu,dnu,snv,cnv,dnv;
  sncndn(z.x,k2,snu,cnu,dnu);
  sncndn(z.y,1.0-k2,snv,cnv,dnv);
  float a = 1.0/(1.0-dnu*dnu*snv*snv);
  return a*vec2(cnu*cnv,-snu*dnu*snv*dnv);
}
// End of mla's CC-NC-BY-SA 4.0 unported code

vec4 spiralize(vec2 fragCoord)
{
    vec2 z = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    float p = .1 * iTime;
    vec2 cs = vec2(cos(p), -sin(p));
    mat2 R = mat2(cs, -cs.y, cs.x);
    z *= R;

    //*/
    /* Jacobi tiles */
    //*
    const float Speed = 2.;
    z = clog(z) * 1.1802 * .5 * float(iJacobiRepeats);
    z.x -= mod(.2 * iTime / float(Speed),1.)*3.7;
    z *= mat2(1,-1,1,1);
    z = cn(z,.5);
    //*/

    return fractal(z * iResolution.y + 0.5 * iResolution.xy);
}

void mainImage(out vec4 outColor, vec2 fragCoord) {
    iCoordinateOffset += 3.*vec2(
        cos(iTime),
        sin(iTime)
    );

    // Vogel-ordered Gauss DOF.
    vec2 uv = (fragCoord.xy - .5 * iResolution.xy) / iResolution.y;
    vec4 col = vec4(0);
    const float gold = 2.4;
    float sampleCount = float(iSampleCount);
    for(float i = .5; i < sampleCount; i += 1.) {
        float x = i / sampleCount;
        float p = gold * i;
        vec2 z =
            // Pixel size.
            .5 / iResolution.y
            // Vogel order.
            * sqrt(x) * vec2(cos(p), sin(p))
        ;
        col += spiralize((uv - z) * iResolution.y + .5 * iResolution.xy);
    }
    outColor = col / sampleCount;
    // Grain.
    outColor += .025 * (2. * hash12(1.e5 * uv) - 1.);
    // Output.
    outColor = clamp(outColor, 0., 1.);
}

