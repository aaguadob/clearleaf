"""PDF Function objects (ISO 32000-1:2008 section 7.10) — used to evaluate
Separation/DeviceN tint transforms and (later) shadings. Four types:
0 (sampled, multilinear interpolation over a stream of sample data), 2
(exponential interpolation), 3 (stitching several sub-functions across
domain pieces), and 4 (a small PostScript-calculator program).
"""

from __future__ import annotations

import math

from ._lexer import Lexer
from ._objects import PDFStream


def _floats(doc, obj) -> list:
    obj = doc.resolve(obj)
    return [float(doc.resolve(x)) for x in obj] if obj else []


class SampledFunction:
    def __init__(self, doc, stream_obj: PDFStream):
        d = stream_obj.dict
        self.domain = _floats(doc, d.get("Domain"))
        self.range = _floats(doc, d.get("Range"))
        self.size = [int(doc.resolve(x)) for x in doc.resolve(d.get("Size"))]
        self.bps = int(doc.resolve(d.get("BitsPerSample")))
        self.n_in = len(self.size)
        self.n_out = len(self.range) // 2
        encode = _floats(doc, d.get("Encode"))
        self.encode = encode or [
            v for i in range(self.n_in) for v in (0.0, float(self.size[i] - 1))
        ]
        self.decode = _floats(doc, d.get("Decode")) or list(self.range)
        self.data = stream_obj.get_data(resolver=doc.resolve)

    def _read_bits(self, bit_offset: int, nbits: int) -> int:
        val = 0
        byte_idx = bit_offset // 8
        bit_in_byte = bit_offset % 8
        bits_read = 0
        data = self.data
        while bits_read < nbits:
            if byte_idx >= len(data):
                break
            byte = data[byte_idx]
            avail = 8 - bit_in_byte
            take = min(avail, nbits - bits_read)
            shift = avail - take
            chunk = (byte >> shift) & ((1 << take) - 1)
            val = (val << take) | chunk
            bits_read += take
            bit_in_byte += take
            if bit_in_byte >= 8:
                bit_in_byte = 0
                byte_idx += 1
        return val

    def _sample_at(self, index_tuple: tuple) -> list:
        flat = 0
        mult = 1
        for i in range(self.n_in):
            flat += index_tuple[i] * mult
            mult *= self.size[i]
        bit_offset = flat * self.n_out * self.bps
        return [
            self._read_bits(bit_offset + j * self.bps, self.bps) for j in range(self.n_out)
        ]

    def eval(self, inputs: list) -> list:
        idx_lo, idx_frac = [], []
        for i in range(self.n_in):
            x = inputs[i]
            dmin, dmax = self.domain[2 * i], self.domain[2 * i + 1]
            x = min(max(x, dmin), dmax)
            emin, emax = self.encode[2 * i], self.encode[2 * i + 1]
            ex = emin + (x - dmin) * (emax - emin) / (dmax - dmin) if dmax != dmin else emin
            ex = min(max(ex, 0.0), float(self.size[i] - 1))
            lo = int(math.floor(ex))
            idx_lo.append(lo)
            idx_frac.append(ex - lo)

        outputs = [0.0] * self.n_out
        for corner in range(1 << self.n_in):
            weight = 1.0
            index_tuple = []
            for i in range(self.n_in):
                if (corner >> i) & 1:
                    index_tuple.append(min(idx_lo[i] + 1, self.size[i] - 1))
                    weight *= idx_frac[i]
                else:
                    index_tuple.append(idx_lo[i])
                    weight *= 1 - idx_frac[i]
            if weight == 0.0:
                continue
            samples = self._sample_at(tuple(index_tuple))
            for j in range(self.n_out):
                outputs[j] += weight * samples[j]

        max_val = float((1 << self.bps) - 1)
        results = []
        for j in range(self.n_out):
            dmin, dmax = self.decode[2 * j], self.decode[2 * j + 1]
            v = dmin + outputs[j] * (dmax - dmin) / max_val if max_val > 0 else dmin
            rmin, rmax = self.range[2 * j], self.range[2 * j + 1]
            results.append(min(max(v, rmin), rmax))
        return results


def _safe_pow(x: float, n: float) -> float:
    try:
        if x < 0:
            return -((-x) ** n) if float(n).is_integer() else 0.0
        return x ** n
    except (ValueError, OverflowError):
        return 0.0


class ExponentialFunction:
    def __init__(self, doc, d: dict):
        self.domain = _floats(doc, d.get("Domain")) or [0.0, 1.0]
        self.c0 = _floats(doc, d.get("C0")) or [0.0]
        self.c1 = _floats(doc, d.get("C1")) or [1.0]
        self.n = float(doc.resolve(d.get("N", 1)))

    def eval(self, inputs: list) -> list:
        x = inputs[0]
        x = min(max(x, self.domain[0]), self.domain[1])
        xn = _safe_pow(x, self.n)
        return [c0 + xn * (c1 - c0) for c0, c1 in zip(self.c0, self.c1)]


class StitchingFunction:
    def __init__(self, doc, d: dict):
        self.domain = _floats(doc, d.get("Domain")) or [0.0, 1.0]
        funcs = doc.resolve(d.get("Functions")) or []
        self.functions = [load_function(doc, f) for f in funcs]
        self.bounds = _floats(doc, d.get("Bounds"))
        self.encode = _floats(doc, d.get("Encode"))

    def eval(self, inputs: list) -> list:
        x = inputs[0]
        dmin, dmax = self.domain[0], self.domain[1]
        x = min(max(x, dmin), dmax)
        idx = 0
        for bound in self.bounds:
            if x < bound:
                break
            idx += 1
        lo = dmin if idx == 0 else self.bounds[idx - 1]
        hi = self.bounds[idx] if idx < len(self.bounds) else dmax
        if idx * 2 + 1 < len(self.encode):
            emin, emax = self.encode[2 * idx], self.encode[2 * idx + 1]
        else:
            emin, emax = 0.0, 1.0
        ex = emin + (x - lo) * (emax - emin) / (hi - lo) if hi != lo else emin
        return self.functions[idx].eval([ex])


class ArrayOfFunctions:
    """A handful of contexts allow an array of 1-in/1-out functions, one
    per output channel, instead of a single n-in/m-out function."""

    def __init__(self, functions: list):
        self.functions = functions

    def eval(self, inputs: list) -> list:
        return [f.eval(inputs)[0] for f in self.functions]


# -- Type 4: PostScript calculator ---------------------------------------


def parse_ps_program(data: bytes) -> list:
    lx = Lexer(data)

    def parse_block() -> list:
        block = []
        while True:
            tok = lx.next_token()
            if tok.kind == "eof":
                raise ValueError("unexpected EOF in PostScript function")
            if tok.kind == "keyword" and tok.value == "}":
                return block
            if tok.kind == "keyword" and tok.value == "{":
                block.append(parse_block())
            elif tok.kind == "num":
                block.append(float(tok.value))
            elif tok.kind == "keyword":
                block.append(tok.value)

    tok = lx.next_token()
    if not (tok.kind == "keyword" and tok.value == "{"):
        raise ValueError("PostScript calculator function must start with '{'")
    return parse_block()


def _apply_ps_op(op: str, stack: list) -> None:
    if op == "add":
        b, a = stack.pop(), stack.pop()
        stack.append(a + b)
    elif op == "sub":
        b, a = stack.pop(), stack.pop()
        stack.append(a - b)
    elif op == "mul":
        b, a = stack.pop(), stack.pop()
        stack.append(a * b)
    elif op == "div":
        b, a = stack.pop(), stack.pop()
        stack.append(a / b if b else 0.0)
    elif op == "idiv":
        b, a = stack.pop(), stack.pop()
        stack.append(float(int(a) // int(b)) if b else 0.0)
    elif op == "mod":
        b, a = stack.pop(), stack.pop()
        stack.append(float(int(a) % int(b)) if b else 0.0)
    elif op == "neg":
        stack.append(-stack.pop())
    elif op == "abs":
        stack.append(abs(stack.pop()))
    elif op == "ceiling":
        stack.append(float(math.ceil(stack.pop())))
    elif op == "floor":
        stack.append(float(math.floor(stack.pop())))
    elif op == "round":
        stack.append(float(round(stack.pop())))
    elif op == "truncate":
        stack.append(float(math.trunc(stack.pop())))
    elif op == "sqrt":
        stack.append(math.sqrt(max(stack.pop(), 0.0)))
    elif op == "sin":
        stack.append(math.sin(math.radians(stack.pop())))
    elif op == "cos":
        stack.append(math.cos(math.radians(stack.pop())))
    elif op == "atan":
        den, num = stack.pop(), stack.pop()
        result = math.degrees(math.atan2(num, den))
        stack.append(result + 360.0 if result < 0 else result)
    elif op == "exp":
        b, a = stack.pop(), stack.pop()
        stack.append(_safe_pow(a, b))
    elif op == "ln":
        a = stack.pop()
        stack.append(math.log(a) if a > 0 else 0.0)
    elif op == "log":
        a = stack.pop()
        stack.append(math.log10(a) if a > 0 else 0.0)
    elif op in ("cvi", "truncate2"):
        stack.append(float(int(stack.pop())))
    elif op == "cvr":
        pass
    elif op == "dup":
        stack.append(stack[-1])
    elif op == "pop":
        stack.pop()
    elif op == "exch":
        b, a = stack.pop(), stack.pop()
        stack.append(b)
        stack.append(a)
    elif op == "copy":
        n = int(stack.pop())
        if n > 0:
            stack.extend(stack[-n:])
    elif op == "index":
        n = int(stack.pop())
        stack.append(stack[-1 - n])
    elif op == "roll":
        j = int(stack.pop())
        n = int(stack.pop())
        if n > 0:
            j %= n
            part = stack[-n:]
            del stack[-n:]
            stack.extend(part[-j:] + part[:-j] if j else part)
    elif op in ("eq", "ne", "gt", "ge", "lt", "le"):
        b, a = stack.pop(), stack.pop()
        stack.append(
            {"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]
        )
    elif op in ("and", "or", "xor"):
        b, a = stack.pop(), stack.pop()
        if isinstance(a, bool) and isinstance(b, bool):
            stack.append({"and": a and b, "or": a or b, "xor": a != b}[op])
        else:
            ia, ib = int(a), int(b)
            stack.append(float({"and": ia & ib, "or": ia | ib, "xor": ia ^ ib}[op]))
    elif op == "not":
        a = stack.pop()
        stack.append((not a) if isinstance(a, bool) else float(~int(a)))
    elif op == "bitshift":
        shift, a = int(stack.pop()), int(stack.pop())
        stack.append(float(a << shift) if shift >= 0 else float(a >> (-shift)))
    elif op == "true":
        stack.append(True)
    elif op == "false":
        stack.append(False)
    # Unknown operators are ignored rather than raising, so a single
    # unsupported op in an otherwise-fine program doesn't nuke the result.


def eval_ps(program: list, stack: list) -> None:
    i = 0
    n = len(program)
    while i < n:
        item = program[i]
        i += 1
        if isinstance(item, float):
            stack.append(item)
        elif isinstance(item, list):
            stack.append(item)
        elif item == "if":
            proc = stack.pop()
            cond = stack.pop()
            if cond:
                eval_ps(proc, stack)
        elif item == "ifelse":
            proc2 = stack.pop()
            proc1 = stack.pop()
            cond = stack.pop()
            eval_ps(proc1 if cond else proc2, stack)
        else:
            _apply_ps_op(item, stack)


class PostScriptFunction:
    def __init__(self, doc, stream_obj: PDFStream):
        d = stream_obj.dict
        self.domain = _floats(doc, d.get("Domain"))
        self.range = _floats(doc, d.get("Range")) or None
        data = stream_obj.get_data(resolver=doc.resolve)
        self.program = parse_ps_program(data)

    def eval(self, inputs: list) -> list:
        clipped = []
        for i, x in enumerate(inputs):
            if 2 * i + 1 < len(self.domain):
                dmin, dmax = self.domain[2 * i], self.domain[2 * i + 1]
                x = min(max(x, dmin), dmax)
            clipped.append(float(x))
        stack = list(clipped)
        eval_ps(self.program, stack)
        n_out = len(self.range) // 2 if self.range else len(stack)
        outputs = stack[-n_out:] if n_out else stack
        results = []
        for j, v in enumerate(outputs):
            v = float(v)
            if self.range:
                rmin, rmax = self.range[2 * j], self.range[2 * j + 1]
                v = min(max(v, rmin), rmax)
            results.append(v)
        return results


def load_function(doc, obj):
    obj = doc.resolve(obj)
    if isinstance(obj, list):
        return ArrayOfFunctions([load_function(doc, f) for f in obj])
    if isinstance(obj, PDFStream):
        d = obj.dict
    elif isinstance(obj, dict):
        d = obj
    else:
        raise ValueError(f"not a function object: {obj!r}")

    ftype = int(doc.resolve(d.get("FunctionType")))
    if ftype == 0:
        return SampledFunction(doc, obj)
    if ftype == 2:
        return ExponentialFunction(doc, d)
    if ftype == 3:
        return StitchingFunction(doc, d)
    if ftype == 4:
        return PostScriptFunction(doc, obj)
    raise ValueError(f"unsupported /FunctionType {ftype}")
