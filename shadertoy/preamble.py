"""Assemble a Shadertoy `mainImage` source into a complete GLSL ES shader.

Shadertoy targets WebGL2 = GLSL ES 3.00, so we wrap with `#version 300 es`
(the V3D driver speaks up to 3.10 — a superset).  The preamble declares every
standard Shadertoy uniform with its exact name; the epilogue supplies main().

Known unsupported (document, don't auto-rewrite):
  - shaders that define their own main() — we own main; Shadertoy code
    defines mainImage().  Detected and warned about.
  - WebGL1-era texture2D()/textureCube() calls — invalid in 300 es; modern
    Shadertoy uses texture().
"""
import re

VERTEX_SHADER = """#version 300 es
void main() {
    // Fullscreen triangle from gl_VertexID — no VBO, no attributes.
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

PREAMBLE = """#version 300 es
// Shadertoy injects this too: 1 on desktop, 0 on mobile/low-power.
// Shaders use it to scale back AA/iterations — right call for the Pi.
#define HW_PERFORMANCE 0
precision highp float;
precision highp int;
precision highp sampler2D;

uniform vec3  iResolution;
uniform float iTime;
uniform float iTimeDelta;
uniform float iFrameRate;
uniform float iSampleRate;
uniform int   iFrame;
uniform vec4  iMouse;
uniform vec4  iDate;
uniform float iChannelTime[4];
uniform vec3  iChannelResolution[4];
uniform sampler2D iChannel0;
uniform sampler2D iChannel1;
uniform sampler2D iChannel2;
uniform sampler2D iChannel3;

out vec4 fragColor_out;
"""

EPILOGUE = """
void main() {
    fragColor_out = vec4(0.0, 0.0, 0.0, 1.0);
    mainImage(fragColor_out, gl_FragCoord.xy);
}
"""

# User source starts on this line (1-based) of the assembled shader.
PREAMBLE_LINES = PREAMBLE.count("\n")

_LINE_REF = re.compile(r"\b0:(\d+)")          # Mesa infolog: "0:LINE(col):"
_USER_MAIN = re.compile(r"\bvoid\s+main\s*\(")
_HAS_MAIN_IMAGE = re.compile(r"\bvoid\s+mainImage\s*\(")


def assemble(user_src):
    """Wrap a Shadertoy mainImage source into a complete fragment shader.

    Returns (source, warnings) — warnings is a list of human-readable strings.
    """
    warnings = []
    if _USER_MAIN.search(user_src):
        warnings.append("shader defines its own main() — the wrapper also "
                        "adds one; Shadertoy shaders should define mainImage() only")
    if not _HAS_MAIN_IMAGE.search(user_src):
        warnings.append("no mainImage() found — this doesn't look like a "
                        "Shadertoy shader; linking will likely fail")
    if "texture2D" in user_src:
        warnings.append("texture2D() is WebGL1-era and invalid in GLSL ES "
                        "3.00 — replace with texture()")
    if not user_src.endswith("\n"):
        user_src += "\n"
    return PREAMBLE + user_src + EPILOGUE, warnings


def remap_log(infolog):
    """Rewrite Mesa GLSL info-log line numbers to user-file line numbers."""
    def fix(m):
        line = int(m.group(1)) - PREAMBLE_LINES
        return f"line {line}" if line > 0 else m.group(0)
    return _LINE_REF.sub(fix, infolog)
