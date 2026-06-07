"""NS-EEL → Python/NumPy transpiler for MilkDrop preset equations.

The same generated code runs in two contexts: scalar (per_frame — variables
are floats) and vectorized (per_pixel / wave per_point — some variables are
NumPy arrays).  All helpers are numpy-based so both work transparently.

Semantics notes (matching ns-eel2 / the MilkDrop authoring guide):
  - All variables are doubles, case-insensitive, default to 0.
  - if(c,a,b) is LAZY in scalar context (only the taken branch executes,
    matters for assignments inside branches); vector context evaluates both
    and selects with np.where — a documented divergence.
  - Division/modulo by zero yield 0; pow/log/asin domain errors yield 0
    instead of NaN (EEL never produces NaN; we sanitize).
  - % and | and & operate on integers (truncated).
  - megabuf(i)/gmegabuf(i) are read/write sparse arrays (scalar context only).
"""
import math
import random

import numpy as np

NUMERIC_CONSTANTS = {"$pi": math.pi, "$e": math.e, "$phi": 1.618033988749895}


class EelError(Exception):
    pass


class EelNS(dict):
    """EEL variable namespace: unknown variables read as 0.0."""
    def __missing__(self, key):
        return 0.0


# ----------------------------------------------------------------------------
# runtime helpers (the generated code's vocabulary)
# ----------------------------------------------------------------------------

_BIG = 1.0e30
_ARR = np.ndarray


def _isarr(*xs):
    for x in xs:
        if type(x) is _ARR:
            return True
    return False


def _fin(x):
    """Sanitize NaN/inf the way EEL avoids them (0 for nan, clamp inf)."""
    if _isarr(x):
        return np.nan_to_num(x, nan=0.0, posinf=_BIG, neginf=-_BIG)
    x = float(x)
    if math.isfinite(x):
        return x
    if x != x:                       # nan
        return 0.0
    return _BIG if x > 0 else -_BIG


def _div(a, b):
    if _isarr(a, b):
        with np.errstate(all="ignore"):
            return _fin(np.where(b == 0, 0.0, np.divide(a, np.where(b == 0, 1.0, b))))
    b = float(b)
    return _fin(float(a) / b) if b != 0.0 else 0.0


def _mod(a, b):
    if _isarr(a, b):
        ai = np.trunc(a)
        bi = np.trunc(b)
        with np.errstate(all="ignore"):
            return np.where(bi == 0, 0.0, np.mod(ai, np.where(bi == 0, 1.0, bi)))
    bi = int(b)
    return float(int(a) % bi) if bi else 0.0


def _pow(a, b):
    if _isarr(a, b):
        with np.errstate(all="ignore"):
            return _fin(np.power(a, b))
    try:
        r = float(a) ** float(b)
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0
    return _fin(r) if type(r) is float else 0.0   # neg base ** frac exp -> complex


def _exp(x):
    if _isarr(x):
        with np.errstate(all="ignore"):
            return _fin(np.exp(x))
    try:
        return math.exp(float(x))
    except OverflowError:
        return _BIG


def _log(x):
    if _isarr(x):
        with np.errstate(all="ignore"):
            return _fin(np.log(np.abs(x) + 1e-30))
    return math.log(abs(float(x)) + 1e-30)


def _log10(x):
    if _isarr(x):
        with np.errstate(all="ignore"):
            return _fin(np.log10(np.abs(x) + 1e-30))
    return math.log10(abs(float(x)) + 1e-30)


def _sqrt(x):
    if _isarr(x):
        return np.sqrt(np.maximum(x, 0.0))
    x = float(x)
    return math.sqrt(x) if x > 0.0 else 0.0


def _invsqrt(x):
    return _div(1.0, _sqrt(x))


def _asin(x):
    if _isarr(x):
        return np.arcsin(np.clip(x, -1.0, 1.0))
    return math.asin(min(1.0, max(-1.0, float(x))))


def _acos(x):
    if _isarr(x):
        return np.arccos(np.clip(x, -1.0, 1.0))
    return math.acos(min(1.0, max(-1.0, float(x))))


def _sigmoid(x, c):
    if _isarr(x, c):
        with np.errstate(all="ignore"):
            return _fin(_div(1.0, 1.0 + np.exp(-x * c)))
    try:
        return 1.0 / (1.0 + math.exp(-float(x) * float(c)))
    except OverflowError:
        return 0.0


def _sin(x):
    return np.sin(x) if _isarr(x) else math.sin(float(x))


def _cos(x):
    return np.cos(x) if _isarr(x) else math.cos(float(x))


def _tan(x):
    return np.tan(x) if _isarr(x) else math.tan(float(x))


def _atan(x):
    return np.arctan(x) if _isarr(x) else math.atan(float(x))


def _atan2(a, b):
    return np.arctan2(a, b) if _isarr(a, b) else math.atan2(float(a), float(b))


def _floor(x):
    return np.floor(x) if _isarr(x) else math.floor(float(x))


def _ceil(x):
    return np.ceil(x) if _isarr(x) else math.ceil(float(x))


def _abs(x):
    return np.abs(x) if _isarr(x) else abs(float(x))


def _trunc(x):
    return np.trunc(x) if _isarr(x) else float(int(x))


def _sign(x):
    if _isarr(x):
        return np.sign(x)
    x = float(x)
    return 0.0 if x == 0.0 else (1.0 if x > 0 else -1.0)


def _min(a, b):
    return np.minimum(a, b) if _isarr(a, b) else (a if a < b else b)


def _max(a, b):
    return np.maximum(a, b) if _isarr(a, b) else (a if a > b else b)


def _rand(x):
    """rand(n): random integer-ish 0..n-1 (scalar even in vector context)."""
    hi = float(np.max(x)) if _isarr(x) else float(x)
    if hi < 1.0:
        return 0.0
    return float(int(random.random() * hi))


def _equal(a, b):
    if _isarr(a, b):
        return (a == b) * 1.0
    return 1.0 if a == b else 0.0


def _above(a, b):
    if _isarr(a, b):
        return (a > b) * 1.0
    return 1.0 if a > b else 0.0


def _below(a, b):
    if _isarr(a, b):
        return (a < b) * 1.0
    return 1.0 if a < b else 0.0


def _band(a, b):
    if _isarr(a, b):
        return np.logical_and(a != 0, b != 0) * 1.0
    return 1.0 if (a != 0 and b != 0) else 0.0


def _bor(a, b):
    if _isarr(a, b):
        return np.logical_or(a != 0, b != 0) * 1.0
    return 1.0 if (a != 0 or b != 0) else 0.0


def _bnot(a):
    if _isarr(a):
        return (a == 0) * 1.0
    return 1.0 if a == 0 else 0.0


def _bitor(a, b):
    return (np.trunc(a).astype(np.int64) | np.trunc(b).astype(np.int64)) * 1.0


def _bitand(a, b):
    return (np.trunc(a).astype(np.int64) & np.trunc(b).astype(np.int64)) * 1.0


def _setv(v, name, value):
    """Assignment-as-expression (a = (b = 3) + 1)."""
    v[name] = value
    return value


def _where(c, a, b):
    """Vector/eager if(); scalar context generates a lazy conditional instead."""
    return np.where(c != 0, a, b)


def _mb_read(buf, idx):
    if not _isarr(idx):
        return buf.get(int(idx), 0.0)
    return np.array([buf.get(int(i), 0.0) for i in np.ravel(idx)],
                    dtype=np.float64).reshape(np.shape(idx))


def _mb_write(buf, idx, value):
    if not _isarr(idx):
        buf[int(idx)] = float(np.max(value)) if _isarr(value) else float(value)
        return value
    vals = np.broadcast_to(value, np.shape(idx)).ravel()
    for i, val in zip(np.ravel(idx), vals):
        buf[int(i)] = float(val)
    return value


def _memcpy(buf, dest, src, length):
    """EEL2 memcpy(dest, src, len) over megabuf address space."""
    d, s, n = int(dest), int(src), int(length)
    for k in range(max(0, min(n, 1_000_000))):
        buf[d + k] = buf.get(s + k, 0.0)
    return dest


def _loop(n, fn):
    """EEL2 loop(count, statements): execute body count times."""
    cnt = int(np.max(n)) if _isarr(n) else int(n)
    for _ in range(max(0, min(cnt, 1_000_000))):
        fn()
    return 0.0


def _while(fn):
    """EEL2 while(statements): repeat until body evaluates to 0."""
    for _ in range(1_000_000):
        r = fn()
        if _isarr(r):
            if not np.any(r != 0):
                break
        elif r == 0:
            break
    return 0.0


_GMEGABUF = {}   # shared across all presets, like EEL's gmegabuf

HELPERS = {
    "np": np,
    "_fin": _fin, "_div": _div, "_mod": _mod, "_pow": _pow, "_exp": _exp,
    "_log": _log, "_log10": _log10, "_sqrt": _sqrt, "_invsqrt": _invsqrt,
    "_asin": _asin, "_acos": _acos, "_sigmoid": _sigmoid, "_rand": _rand,
    "_sin": _sin, "_cos": _cos, "_tan": _tan, "_atan": _atan, "_atan2": _atan2,
    "_floor": _floor, "_ceil": _ceil, "_abs": _abs, "_trunc": _trunc,
    "_sign": _sign, "_min": _min, "_max": _max,
    "_equal": _equal, "_above": _above, "_below": _below,
    "_band": _band, "_bor": _bor, "_bnot": _bnot,
    "_bitor": _bitor, "_bitand": _bitand,
    "_setv": _setv, "_where": _where,
    "_mb_read": _mb_read, "_mb_write": _mb_write,
    "_loop": _loop, "_while": _while, "_memcpy": _memcpy,
}

# function name -> (codegen template or helper, arg count or None=variadic>=1)
FUNCS = {
    "sin": ("_sin({0})", 1), "cos": ("_cos({0})", 1), "tan": ("_tan({0})", 1),
    "asin": ("_asin({0})", 1), "acos": ("_acos({0})", 1), "atan": ("_atan({0})", 1),
    "atan2": ("_atan2({0}, {1})", 2),
    "sqr": ("(({0})*({0}))", 1), "sqrt": ("_sqrt({0})", 1),
    "invsqrt": ("_invsqrt({0})", 1), "rsqrt": ("_invsqrt({0})", 1),
    "pow": ("_pow({0}, {1})", 2), "exp": ("_exp({0})", 1),
    "log": ("_log({0})", 1), "log10": ("_log10({0})", 1),
    "floor": ("_floor({0})", 1), "ceil": ("_ceil({0})", 1),
    "abs": ("_abs({0})", 1), "fabs": ("_abs({0})", 1),
    "min": ("_min({0}, {1})", 2), "max": ("_max({0}, {1})", 2),
    "sign": ("_sign({0})", 1), "rand": ("_rand({0})", 1),
    "int": ("_trunc({0})", 1), "trunc": ("_trunc({0})", 1),
    "frac": ("(({0}) - _floor({0}))", 1),
    "sigmoid": ("_sigmoid({0}, {1})", 2),
    "equal": ("_equal({0}, {1})", 2), "above": ("_above({0}, {1})", 2),
    "below": ("_below({0}, {1})", 2),
    "band": ("_band({0}, {1})", 2), "bor": ("_bor({0}, {1})", 2),
    "bnot": ("_bnot({0})", 1), "bnot2": ("_bnot({0})", 1),
    "exec2": ("(({0}), ({1}))[1]", 2), "exec3": ("(({0}), ({1}), ({2}))[2]", 3),
    # if / megabuf handled specially in codegen
}

# ----------------------------------------------------------------------------
# tokenizer
# ----------------------------------------------------------------------------

_TWO_CHAR = {"==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%="}
_ONE_CHAR = set("+-*/%^|&=<>!?:;,()[]")


def tokenize(src):
    """Yield (kind, text) tokens; kind in num/ident/op.  Strips // comments."""
    tokens = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            if j < n and src[j] in "eE" and j + 1 < n and (src[j + 1].isdigit() or src[j + 1] in "+-"):
                j += 2
                while j < n and src[j].isdigit():
                    j += 1
            tokens.append(("num", src[i:j]))
            i = j
            continue
        if c.isalpha() or c == "_" or c == "$":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            tokens.append(("ident", src[i:j].lower()))
            i = j
            continue
        two = src[i:i + 2]
        if two in _TWO_CHAR:
            tokens.append(("op", two))
            i += 2
            continue
        if c in _ONE_CHAR:
            tokens.append(("op", c))
            i += 1
            continue
        raise EelError(f"unexpected character {c!r} at offset {i}")
    return tokens


# ----------------------------------------------------------------------------
# parser / codegen (recursive descent, emits Python expression strings)
# ----------------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens, scalar):
        self.toks = tokens
        self.pos = 0
        self.scalar = scalar      # lazy if() + allowed megabuf in scalar context

    def peek(self, k=0):
        p = self.pos + k
        return self.toks[p] if p < len(self.toks) else (None, None)

    def next(self):
        t = self.peek()
        self.pos += 1
        return t

    def expect(self, text):
        kind, t = self.next()
        if t != text:
            raise EelError(f"expected {text!r}, got {t!r}")

    # statements ---------------------------------------------------------
    def parse_statements(self):
        stmts = []
        while self.pos < len(self.toks):
            if self.peek()[1] == ";":
                self.next()
                continue
            stmts.append(self.parse_assign())
            if self.peek()[1] == ";":
                self.next()
            # else: lenient — some presets omit the ';' at line ends and real
            # MilkDrop still loaded them; treat the boundary as implicit
        return stmts

    # expression grammar ---------------------------------------------------
    RESERVED = ("if", "megabuf", "gmegabuf", "gmem", "loop", "while", "assign")

    def parse_assign(self):
        # lookahead: ident (=|+=|...) ...   (function-named idents may be
        # plain variables when not followed by '(' — presets do use them)
        kind, t = self.peek()
        if kind == "ident" and t not in self.RESERVED:
            nk, nt = self.peek(1)
            if nt in ("=", "+=", "-=", "*=", "/=", "%="):
                self.next()
                _, op = self.next()
                rhs = self.parse_assign()       # right-assoc chain a=b=0
                var = f"v[{t!r}]"
                if op == "=":
                    return f"_setv(v, {t!r}, ({rhs}))"
                py_op = {"+=": "+", "-=": "-", "*=": "*"}.get(op)
                if py_op:
                    return f"_setv(v, {t!r}, ({var} {py_op} ({rhs})))"
                if op == "/=":
                    return f"_setv(v, {t!r}, _div({var}, ({rhs})))"
                return f"_setv(v, {t!r}, _mod({var}, ({rhs})))"
        e = self.parse_ternary()
        # memory-target writes: megabuf(i)=x, gmem[i]=x, expr[i]+=x ...
        # parse_primary yields these reads as "_mb_read(BUF, ADDR)"; if an
        # assignment op follows, rewrite the read into a write.
        op = self.peek()[1]
        if op in ("=", "+=", "-=", "*=", "/=", "%=") and e.startswith("_mb_read("):
            self.next()
            rhs = self.parse_assign()
            inner = e[len("_mb_read("):-1]
            buf, addr = inner.split(", ", 1)
            if op == "=":
                val = f"({rhs})"
            elif op == "+=":
                val = f"({e} + ({rhs}))"
            elif op == "-=":
                val = f"({e} - ({rhs}))"
            elif op == "*=":
                val = f"({e} * ({rhs}))"
            elif op == "/=":
                val = f"_div({e}, ({rhs}))"
            else:
                val = f"_mod({e}, ({rhs}))"
            return f"_mb_write({buf}, {addr}, {val})"
        return e

    def parse_arg(self):
        """One function argument — may be a ';'-separated statement list
        (loop bodies, if branches); value is the last statement's."""
        exprs = [self.parse_assign()]
        while self.peek()[1] == ";":
            while self.peek()[1] == ";":      # skip empty statements (;;)
                self.next()
            if self.peek()[1] in (",", ")", "]", None):
                break
            exprs.append(self.parse_assign())
        if len(exprs) == 1:
            return exprs[0]
        return f"(({', '.join(exprs)})[-1])"

    def parse_ternary(self):
        cond = self.parse_or()
        if self.peek()[1] == "?":
            self.next()
            a = self.parse_assign()
            self.expect(":")
            b = self.parse_assign()
            return self._ifexpr(cond, a, b)
        return cond

    def parse_or(self):
        e = self.parse_and()
        while self.peek()[1] == "||":
            self.next()
            e = f"_bor(({e}), ({self.parse_and()}))"
        return e

    def parse_and(self):
        e = self.parse_bitor()
        while self.peek()[1] == "&&":
            self.next()
            e = f"_band(({e}), ({self.parse_bitor()}))"
        return e

    def parse_bitor(self):
        e = self.parse_bitand()
        while self.peek()[1] == "|":
            self.next()
            e = f"_bitor(({e}), ({self.parse_bitand()}))"
        return e

    def parse_bitand(self):
        e = self.parse_equality()
        while self.peek()[1] == "&":
            self.next()
            e = f"_bitand(({e}), ({self.parse_equality()}))"
        return e

    def parse_equality(self):
        e = self.parse_relational()
        while self.peek()[1] in ("==", "!="):
            _, op = self.next()
            rhs = self.parse_relational()
            fn = "_equal" if op == "==" else None
            e = f"_equal(({e}), ({rhs}))" if fn else f"_bnot(_equal(({e}), ({rhs})))"
        return e

    def parse_relational(self):
        e = self.parse_additive()
        while self.peek()[1] in ("<", ">", "<=", ">="):
            _, op = self.next()
            rhs = self.parse_additive()
            e = f"((({e}) {op} ({rhs})) * 1.0)"
        return e

    def parse_additive(self):
        e = self.parse_multiplicative()
        while self.peek()[1] in ("+", "-"):
            _, op = self.next()
            e = f"(({e}) {op} ({self.parse_multiplicative()}))"
        return e

    def parse_multiplicative(self):
        e = self.parse_unary()
        while self.peek()[1] in ("*", "/", "%"):
            _, op = self.next()
            rhs = self.parse_unary()
            if op == "*":
                e = f"(({e}) * ({rhs}))"
            elif op == "/":
                e = f"_div(({e}), ({rhs}))"
            else:
                e = f"_mod(({e}), ({rhs}))"
        return e

    def parse_unary(self):
        kind, t = self.peek()
        if t == "-":
            self.next()
            return f"(-({self.parse_unary()}))"
        if t == "+":
            self.next()
            return self.parse_unary()
        if t == "!":
            self.next()
            return f"_bnot({self.parse_unary()})"
        return self.parse_power()

    def parse_power(self):
        e = self.parse_primary()
        if self.peek()[1] == "^":
            self.next()
            return f"_pow(({e}), ({self.parse_unary()}))"   # right-assoc
        return e

    def parse_primary(self):
        return self._postfix(self._atom())

    def _postfix(self, e):
        """EEL2 memory syntax: expr[idx] reads megabuf at address expr+idx."""
        while self.peek()[1] == "[":
            self.next()
            if self.peek()[1] == "]":
                idx = "0.0"
            else:
                idx = self.parse_arg()
            self.expect("]")
            if e == "__GMEM__":
                e = f"_mb_read(_GMB, ({idx}))"
            else:
                e = f"_mb_read(_MB, (({e}) + ({idx})))"
        return e

    def _atom(self):
        kind, t = self.next()
        if kind == "num":
            return repr(float(t))
        if t == "(":
            # parenthesized group may contain assignments (a = (b=3)+1) and
            # even statement lists: (t1 = x; t2 = y;) evaluates to the last
            exprs = [self.parse_assign()]
            while self.peek()[1] == ";":
                while self.peek()[1] == ";":  # skip empty statements (;;)
                    self.next()
                if self.peek()[1] == ")":
                    break
                exprs.append(self.parse_assign())
            self.expect(")")
            if len(exprs) == 1:
                return f"({exprs[0]})"
            return f"(({', '.join(exprs)})[-1])"
        if kind != "ident":
            raise EelError(f"unexpected token {t!r}")
        if t in NUMERIC_CONSTANTS:
            return repr(NUMERIC_CONSTANTS[t])
        if t == "if":
            self.expect("(")
            cond = self.parse_arg()
            self.expect(",")
            a = self.parse_arg()
            self.expect(",")
            b = self.parse_arg()
            self.expect(")")
            return self._ifexpr(cond, a, b)
        if t == "gmem":
            if self.peek()[1] != "[":
                return "v['gmem']"
            return "__GMEM__"          # resolved by _postfix
        if t in ("loop", "while"):
            self.expect("(")
            if t == "loop":
                count = self.parse_arg()
                self.expect(",")
                body = self.parse_arg()
                self.expect(")")
                return f"_loop(({count}), lambda: ({body}))"
            body = self.parse_arg()
            self.expect(")")
            return f"_while(lambda: ({body}))"
        if t == "assign":                       # assign(dest, src) — rare
            self.expect("(")
            kind2, name = self.next()
            self.expect(",")
            rhs = self.parse_arg()
            self.expect(")")
            return f"_setv(v, {name!r}, ({rhs}))"
        if t == "memcpy":
            self.expect("(")
            dest = self.parse_arg(); self.expect(",")
            srca = self.parse_arg(); self.expect(",")
            ln = self.parse_arg(); self.expect(")")
            return f"_memcpy(_MB, ({dest}), ({srca}), ({ln}))"
        if t in ("megabuf", "gmegabuf"):
            self.expect("(")
            idx = self.parse_arg()
            self.expect(")")
            buf = "_GMB" if t == "gmegabuf" else "_MB"
            return f"_mb_read({buf}, ({idx}))"
        if t in FUNCS and self.peek()[1] == "(":
            template, argc = FUNCS[t]
            self.expect("(")
            args = [self.parse_arg()]
            while self.peek()[1] == ",":
                self.next()
                args.append(self.parse_arg())
            self.expect(")")
            if argc is not None and len(args) != argc:
                # min/max occasionally appear with >2 args; fold left
                if t in ("min", "max") and len(args) > 2:
                    e = args[0]
                    for a in args[1:]:
                        e = template.format(e, a)
                    return e
                raise EelError(f"{t}() takes {argc} args, got {len(args)}")
            return template.format(*args)
        if self.peek()[1] == "(":
            raise EelError(f"unknown function {t}()")
        return f"v[{t!r}]"

    # helpers ---------------------------------------------------------------
    def _ifexpr(self, cond, a, b):
        if self.scalar:
            # lazy: only the taken branch's side effects run (EEL semantics)
            return f"(({a}) if np.any(({cond}) != 0) else ({b}))"
        return f"_where(({cond}), ({a}), ({b}))"



class CompiledEel:
    """A compiled EEL block.  run(ns, megabuf) executes it against a
    namespace (dict with __missing__ -> 0.0)."""

    def __init__(self, source_lines, name):
        self.name = name
        self.source = "\n".join(source_lines)
        self.code = compile(self.source, f"<eel:{name}>", "exec")

    def run(self, ns, megabuf=None):
        g = dict(HELPERS)
        g["v"] = ns
        g["_MB"] = megabuf if megabuf is not None else {}
        g["_GMB"] = _GMEGABUF
        exec(self.code, g)


def transpile(code, name="eel", scalar=True):
    """EEL source block -> CompiledEel.  Raises EelError on unsupported input."""
    tokens = tokenize(code)
    if not tokens:
        return None
    parser = _Parser(tokens, scalar=scalar)
    stmts = parser.parse_statements()
    # every statement is an expression (assignments are _setv calls), so each
    # line is a valid Python expression statement
    return CompiledEel(stmts, name)
