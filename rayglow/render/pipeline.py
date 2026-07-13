"""ShaderToy: owns the GL pieces and turns (shader sources, t) into frames.

Multipass: passes render in Shadertoy order bufA -> bufB -> bufC -> bufD ->
image every frame.  Channel bindings come from `// iChannelN: spec` comment
directives in each pass's source (re-applied on hot reload) plus CLI
--channelN overrides for the image pass.
"""
import datetime

from . import output
from . import passes
from . import textures
from .output import Readback

BUFFER_ORDER = ("bufA", "bufB", "bufC", "bufD")


class _UniformState:
    __slots__ = ("time", "dt", "frame", "frame_rate", "mouse", "date")


def _idate():
    """Shadertoy iDate: (year, month 0-based, day, seconds since midnight)."""
    now = datetime.datetime.now()
    secs = (now.hour * 3600 + now.minute * 60 + now.second
            + now.microsecond * 1e-6)
    return (float(now.year), float(now.month - 1), float(now.day), secs)


class ShaderToy:
    """Shadertoy renderer: single-pass by default, multipass via add_buffer().

    The GLContext must already be current.  render(t, dt, frame) returns a
    panel-ready (H, W, 3) uint8 numpy array.
    """

    def __init__(self, width, height, scale=2, gamma=1.0, base_dir=None,
                 readback="auto", resolve_flip=(False, False), use_pbo=False,
                 quiet=False):
        """readback: 'auto' | 'dmabuf' | 'dmabuf-pipe' | 'glread' use the GPU
        resolve pass (downsample+gamma+orientation on V3D) and the matching
        reader — `gamma` is baked into that pass, so feed the packer
        hub75.LUT_IDENTITY when it isn't 1.0.  'legacy' is the original
        full-size glReadPixels + CPU postprocess path (honors use_pbo);
        there `gamma` is applied by the CPU LUT as before."""
        self.width, self.height, self.scale = width, height, scale
        self.base_dir = base_dir          # directive image paths resolve here
        # Unused samplers bind to this 1x1 black texture so they're valid.
        self.dummy_tex = passes.make_texture(1, 1, bytes(4))
        self.passes = {"image": passes.Pass("image", width * scale,
                                            height * scale, self.dummy_tex)}
        self.legacy = readback == "legacy"
        if self.legacy:
            self.readback = Readback(width, height, scale, gamma,
                                     use_pbo=use_pbo)
            self.resolve = self.reader = None
        else:
            self.readback = None
            self.resolve = output.make_resolve(
                width, height, scale, gamma,
                self.passes["image"].out_tex, self.dummy_tex,
                flip=resolve_flip)
            # render() repoints resolve.fbo at the reader's per-frame target;
            # remember the pass's own FBO so destroy() frees the right one.
            self._resolve_home_fbo = self.resolve.fbo
            self.reader, desc = output.make_reader(
                readback, self.resolve.fbo, width, height)
            if not quiet:
                print(f"readback: {desc}")
        self.audio_channels = []          # live list; AudioFeed iterates it
        self.buffer_format = passes.pick_buffer_format()
        self._cli_specs = {}              # image-pass overrides {index: spec}
        self._bound = {}                  # (pass_name, index) -> spec
        self._cache = {}                  # spec -> Channel (shared textures)
        self._smoothed_fps = 60.0

    def destroy(self):
        """Free every GL object this toy owns: passes (programs + render
        targets), the resolve pass + reader (dmabuf fds/EGLImages) or the
        legacy readback's PBOs, cached channel textures, and the dummy
        texture.  Call before dropping a toy (e.g. --loop switch) so rebuilds
        don't leak FBOs/textures into the Pi's shared RAM.  Buffer channels
        own no texture; texture-backed channels each own one."""
        for p in self.passes.values():
            p.destroy()
        from .egl import delete_texture
        for ch in self._cache.values():
            delete_texture(getattr(ch, "texture", 0))
        delete_texture(self.dummy_tex)
        if self.legacy:
            self.readback.destroy()
        else:
            self.resolve.fbo = self._resolve_home_fbo
            self.resolve.destroy()
            self.reader.destroy()
        self.passes = {}
        self._cache = {}
        self.audio_channels = []

    @property
    def image(self):
        return self.passes["image"]

    def add_buffer(self, name):
        """Create a double-buffered float pass (name in BUFFER_ORDER)."""
        internal, data_type, filt, _label = self.buffer_format
        self.passes[name] = passes.Pass(
            name, self.width * self.scale, self.height * self.scale,
            self.dummy_tex, double_buffered=True, internal=internal,
            data_type=data_type, filt=filt)

    def set_cli_channel(self, index, spec):
        """--channelN: overrides any directive on the image pass."""
        self._cli_specs[index] = spec

    def set_source(self, name, user_src):
        """Compile/swap one pass's shader, then (re)bind its channels from
        the source's directives.  (ok, message) — see Pass.compile."""
        ok, msg = self.passes[name].compile(user_src)
        if ok:
            warnings = self._bind_channels(name, user_src)
            if warnings:
                msg = "\n".join(filter(None, [msg] + warnings))
        return ok, msg

    # -- channels -------------------------------------------------------------
    def _channel(self, spec):
        """Spec string -> Channel, cached so passes share textures."""
        if spec in self._cache:
            return self._cache[spec]
        if spec in BUFFER_ORDER:
            if spec not in self.passes:
                raise ValueError(f"no {spec} pass (missing .{spec}.glsl file)")
            ch = passes.BufferChannel(self.passes, spec)
        else:
            ch = textures.parse_channel_spec(spec, self.base_dir)
            if getattr(ch, "feed_driven", False):    # audio + milk channels
                self.audio_channels.append(ch)
        self._cache[spec] = ch
        return ch

    def _bind_channels(self, name, user_src):
        """Apply `// iChannelN:` directives (+ CLI overrides on the image
        pass).  Returns warning strings; a bad spec binds black, not fatal."""
        specs = textures.parse_directives(user_src)
        if name == "image":
            specs.update(self._cli_specs)
        warnings = []
        p = self.passes[name]
        for i in range(4):
            spec = specs.get(i)
            if spec == "self":
                if name == "image":
                    warnings.append("iChannel%d: 'self' is only valid in "
                                    "buffer passes" % i)
                    spec = None
                else:
                    spec = name
            if self._bound.get((name, i)) == spec:
                continue
            if spec is None:
                p.channels[i] = passes.Channel(texture=self.dummy_tex)
            else:
                try:
                    p.channels[i] = self._channel(spec)
                    print(f"{name} iChannel{i} <- {spec} "
                          f"({p.channels[i].kind})")
                except Exception as e:
                    warnings.append(f"iChannel{i} {spec!r}: {e} — bound black")
                    p.channels[i] = passes.Channel(texture=self.dummy_tex)
                    spec = None
            self._bound[(name, i)] = spec
        return warnings

    # -- per-frame ------------------------------------------------------------
    def render(self, t, dt, frame, mouse=(0.0, 0.0, 0.0, 0.0)):
        if dt > 1e-6:
            self._smoothed_fps += 0.1 * (1.0 / dt - self._smoothed_fps)
        st = _UniformState()
        st.time, st.dt, st.frame = t, dt, frame
        st.frame_rate = self._smoothed_fps
        st.mouse = mouse
        st.date = _idate()
        for name in BUFFER_ORDER:
            p = self.passes.get(name)
            if p is not None:
                p.render(st, self.passes)
        self.image.render(st, self.passes)
        if self.legacy:
            return self.readback.read(self.image.fbo)
        # Resolve pass: image texture -> panel-resolution frame, rendered
        # into whichever target the reader wants this frame (dmabuf
        # ping-pong slots, or its own texture FBO for glread).
        self.resolve.fbo = self.reader.target_fbo(frame)
        self.resolve.render(st)
        return self.reader.read(frame)
