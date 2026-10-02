"""Build-time macros only: emit an OS made solely of ><+-.,[].

There is no Python, macro language, function dispatcher, or shell service in
the native shim. Even the dispatcher below executes as ordinary Brainfuck.
"""
from contextlib import contextmanager
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
STRIDE = 8


class Generator:
    def __init__(self):
        self.variables = {}
        self.next_var = 80
        self.free = list(range(16, 80))
        self.blocks = []
        self.routines = {}
        self.arrays = {}
        self.next_array = 1024
        self.array("flags", 1024)
        self.array("retlo", 32)
        self.array("rethi", 32)
        self.array("cmd", 64)
        self.array("history", 64)
        self.array("name", 16)
        self.array("dest", 16)
        self.array("dir", 512)
        self.array("file", 2048)
        self.array("user", 4096)

    def var(self, name):
        if isinstance(name, int):
            return name
        if name not in self.variables:
            self.variables[name] = self.next_var
            self.next_var += 1
            assert self.next_var < 1024
        return self.variables[name]

    def word(self, name):
        return self.var(name + "0"), self.var(name + "1")

    def array(self, name, size):
        base = self.next_array + STRIDE  # zero guard record on both sides
        self.arrays[name] = base, size
        self.next_array = base + STRIDE * size + STRIDE
        assert self.next_array < 65536

    def block(self, name):
        number = len(self.blocks)
        assert number < self.arrays["flags"][1], "Too many BF continuation states"
        self.blocks.append({"name": name, "code": ""})
        return number

    def entry(self, name):
        if name not in self.routines:
            self.routines[name] = self.block(name)
        return self.routines[name]

    def routine(self, name):
        return Builder(self, self.entry(name), name)


class Emitter:
    def __init__(self, gen):
        self.g = gen
        self.p = 0
        self.parts = []

    def raw(self, code):
        self.parts.append(code)

    def go(self, cell):
        cell = self.g.var(cell)
        diff = cell - self.p
        self.raw(">" * diff if diff >= 0 else "<" * -diff)
        self.p = cell

    def clear(self, cell):
        self.go(cell)
        self.raw("[-]")

    def add(self, cell, value=1):
        self.go(cell)
        value %= 256
        self.raw("+" * value if value <= 128 else "-" * (256 - value))

    def set(self, cell, value):
        self.clear(cell)
        self.add(cell, value)

    @contextmanager
    def temp(self, count=1):
        assert len(self.g.free) >= count
        cells = [self.g.free.pop() for _ in range(count)]
        for cell in cells:
            self.clear(cell)
        yield cells[0] if count == 1 else cells
        for cell in cells:
            self.clear(cell)
        self.g.free.extend(reversed(cells))

    def move(self, source, dest):
        self.go(source)
        self.raw("[")
        self.add(source, -1)
        self.add(dest)
        self.go(source)
        self.raw("]")

    def copy(self, source, dest):
        if self.g.var(source) == self.g.var(dest):
            return
        self.clear(dest)
        with self.temp() as temp:
            self.go(source)
            self.raw("[")
            self.add(source, -1)
            self.add(dest)
            self.add(temp)
            self.go(source)
            self.raw("]")
            self.move(temp, source)

    def loop(self, cell, body):
        self.go(cell)
        self.raw("[")
        body()
        self.go(cell)
        self.raw("]")

    def once(self, cell, body):
        with self.temp() as flag:
            self.copy(cell, flag)
            def execute():
                self.clear(flag)
                body()
            self.loop(flag, execute)

    def otherwise(self, cell, yes, no=lambda: None):
        with self.temp() as flag:
            self.set(flag, 1)
            self.once(cell, lambda: (self.clear(flag), yes()))
            self.once(flag, no)

    def zero(self, source, dest):
        self.set(dest, 1)
        self.once(source, lambda: self.clear(dest))

    def nonzero(self, source, dest):
        self.clear(dest)
        self.once(source, lambda: self.set(dest, 1))

    def eq(self, source, value, dest):
        with self.temp() as temp:
            self.copy(source, temp)
            self.add(temp, -value)
            self.zero(temp, dest)

    def equal(self, a, b, dest):
        with self.temp(2) as (x, y):
            self.copy(a, x)
            self.copy(b, y)
            self.loop(y, lambda: (self.add(y, -1), self.add(x, -1)))
            self.zero(x, dest)

    def less(self, a, b, dest):
        # Low-to-high bit comparison avoids repeatedly copying a large byte
        # inside a decrement loop (which would make comparison quadratic).
        with self.temp(5) as (x, y, abit, bbit, same):
            self.copy(a, x)
            self.copy(b, y)
            self.clear(dest)
            for _ in range(8):
                self.halve_byte(x,abit)
                self.halve_byte(y,bbit)
                self.equal(abit,bbit,same)
                self.otherwise(same,lambda:None,lambda:self.copy(bbit,dest))

    def halve_byte(self, value, bit):
        with self.temp(2) as (count, parity):
            self.copy(value,count); self.clear(value); self.clear(parity)
            def consume():
                self.add(count,-1)
                self.otherwise(parity,lambda:(self.clear(parity),self.add(value)),lambda:self.set(parity,1))
            self.loop(count,consume); self.copy(parity,bit)

    def wset(self, word, value):
        self.set(word[0], value & 255)
        self.set(word[1], value >> 8)

    def wcopy(self, source, dest):
        self.copy(source[0], dest[0])
        self.copy(source[1], dest[1])

    def wnz(self, word, dest):
        self.nonzero(word[0], dest)
        self.once(word[1], lambda: self.set(dest, 1))

    def winc(self, word):
        self.add(word[0])
        with self.temp() as flag:
            self.zero(word[0], flag)
            self.once(flag, lambda: self.add(word[1]))

    def wdec(self, word):
        with self.temp() as flag:
            self.zero(word[0], flag)
            self.once(flag, lambda: self.add(word[1], -1))
        self.add(word[0], -1)

    def weq(self, a, b, dest):
        self.equal(a[0], b[0], dest)
        with self.temp() as flag:
            self.equal(a[1], b[1], flag)
            self.otherwise(flag, lambda: None, lambda: self.clear(dest))

    def wless(self, a, b, dest):
        with self.temp() as same:
            self.equal(a[1], b[1], same)
            self.otherwise(same, lambda: self.less(a[0], b[0], dest),
                           lambda: self.less(a[1], b[1], dest))

    def wadd(self, a, b, overflow=None):
        with self.temp(2) as (count, flag):
            self.copy(b[0], count)
            def low():
                self.add(count, -1)
                self.winc(a)
                if overflow is not None:
                    self.wnz(a, flag)
                    self.otherwise(flag, lambda: None, lambda: self.set(overflow, 1))
            self.loop(count, low)
            self.copy(b[1], count)
            def high():
                self.add(count, -1)
                self.add(a[1])
                if overflow is not None:
                    self.zero(a[1], flag)
                    self.once(flag, lambda: self.set(overflow, 1))
            self.loop(count, high)

    def wsub(self, a, b):
        with self.temp() as count:
            self.copy(b[0], count)
            self.loop(count, lambda: (self.add(count, -1), self.wdec(a)))
            self.copy(b[1], count)
            self.loop(count, lambda: (self.add(count, -1), self.add(a[1], -1)))

    def halve(self, word, bit):
        with self.temp(2) as (count, parity):
            self.copy(word[0],count); self.clear(word[0]); self.clear(parity)
            def low():
                self.add(count,-1)
                self.otherwise(parity,lambda:(self.clear(parity),self.add(word[0])),lambda:self.set(parity,1))
            self.loop(count,low); self.copy(parity,bit)
            self.copy(word[1],count); self.clear(word[1]); self.clear(parity)
            def high():
                self.add(count,-1)
                self.otherwise(parity,lambda:(self.clear(parity),self.add(word[1])),lambda:self.set(parity,1))
            self.loop(count,high); self.once(parity,lambda:self.add(word[0],128))

    def hw(self, operation):
        self.set(0, operation)
        for cell in range(16):
            self.go(cell)
            self.raw(".")
        self.go(0)

    def array_clear(self, name):
        base, size = self.g.arrays[name]
        for index in range(size):
            self.clear(base + index*STRIDE + 4)

    def flag(self, number):
        self.set(self.g.arrays["flags"][0] + STRIDE * number + 4, 1)

    def array_access(self, name, index, value, write=False):
        """16-bit indirect access implemented entirely by BF breadcrumbs.

        Each record: remaining low/high, breadcrumb, transported payload,
        value, scratch, traversal condition, copy scratch. Records and guards
        return to zero except for stored values, including after index zero.
        """
        base, size = self.g.arrays[name]
        self.copy(index[0], base)
        self.copy(index[1], base+1)
        if write:
            self.copy(value, base+3)
        else:
            self.clear(base+3)
        # Local code has a moving origin: its end pointer is deliberately in
        # the next record. The terminating record gives us the dynamic address.
        def local_copy(e, source, dest, scratch):
            e.clear(dest)
            e.clear(scratch)
            e.loop(source, lambda: (e.add(source, -1), e.add(dest), e.add(scratch)))
            e.move(scratch, source)
        def active(e, origin=0):
            e.clear(origin+6)
            local_copy(e, origin, origin+5, origin+7)
            e.loop(origin+5, lambda: (e.clear(origin+5), e.set(origin+6, 1)))
            local_copy(e, origin+1, origin+5, origin+7)
            e.loop(origin+5, lambda: (e.clear(origin+5), e.set(origin+6, 1)))
        local = Emitter(self.g)
        active(local)
        local.go(6)
        local.raw("[")
        local.clear(6)
        local.set(5, 1)
        local_copy(local, 0, 7, 6)
        local.loop(7, lambda: (local.clear(7), local.clear(5)))
        local.loop(5, lambda: (local.clear(5), local.add(1, -1)))
        local.add(0, -1)
        local.move(0, 8)
        local.move(1, 9)
        local.move(3, 11)
        local.set(2, 1)
        active(local, 8)
        local.go(14)
        local.raw("]")
        local.p = 6  # normalize to the dynamic terminal record
        if write:
            local.clear(4)
            local.move(3, 4)
        else:
            local_copy(local, 4, 3, 5)
        local.go(-6)  # previous record's breadcrumb
        local.raw("[")
        local.add(-6, -1)
        local.move(3, -5)
        local.go(-14)
        local.raw("]")
        local.p = -6  # normalized zero guard's breadcrumb
        local.go(3)
        self.go(base)
        self.raw("".join(local.parts))
        self.p = base+3
        if not write:
            self.clear(value)
            self.move(base+3, value)

    def result(self):
        return "".join(self.parts)


class Builder:
    def __init__(self, gen, entry, name):
        self.g, self.current, self.name = gen, entry, name
        self.e = Emitter(gen)

    def new(self, label):
        return self.g.block(self.name + "/" + label)

    def finish(self):
        if self.current is not None:
            self.g.blocks[self.current]["code"] += self.e.result()
            self.g.blocks[self.current]["end"] = self.e.p
        self.current = None
        self.e = Emitter(self.g)

    def at(self, number):
        self.finish()
        self.current = number

    def goto(self, number):
        if self.current is not None:
            self.e.flag(number)
            self.finish()
            self.current = None

    def call(self, name):
        continuation = self.new("after " + name)
        depth = self.g.word("depth")
        value = self.g.var("return_value")
        self.e.set(value, continuation & 255)
        self.e.array_access("retlo", depth, value, True)
        self.e.set(value, continuation >> 8)
        self.e.array_access("rethi", depth, value, True)
        self.e.winc(depth)
        self.goto(self.g.entry(name))
        self.at(continuation)

    def ret(self):
        depth = self.g.word("depth")
        address = self.g.word("return_address")
        self.e.wdec(depth)
        self.e.array_access("retlo", depth, address[0])
        self.e.array_access("rethi", depth, address[1])
        # One shared BF binary routing tree avoids a linear walk through the
        # continuation flags on every return. Still just ordinary BF loops.
        self.e.flag(self.g.entry("return_dispatch"))
        self.finish()
        self.current = None

    def if_(self, condition, yes, no=lambda b: None):
        left, right, end = self.new("yes"), self.new("no"), self.new("join")
        self.e.otherwise(condition, lambda: self.e.flag(left), lambda: self.e.flag(right))
        self.finish()
        self.at(left)
        yes(self)
        self.goto(end)
        self.at(right)
        no(self)
        self.goto(end)
        self.at(end)

    def while_(self, condition, body):
        check, work, end = self.new("check"), self.new("loop"), self.new("end")
        self.goto(check)
        self.at(check)
        flag = self.g.var("condition")
        condition(self.e, flag)
        self.e.otherwise(flag, lambda: self.e.flag(work), lambda: self.e.flag(end))
        self.finish()
        self.at(work)
        body(self)
        self.goto(check)
        self.at(end)

    def msg(self, text):
        literal(self.e, text)

    def get(self, array, index, dest):
        self.e.set("array", list(self.g.arrays).index(array))
        self.e.wcopy(index, self.g.word("index"))
        self.call("get")
        self.e.copy("value", dest)

    def put(self, array, index, source):
        self.e.set("array", list(self.g.arrays).index(array))
        self.e.wcopy(index, self.g.word("index"))
        self.e.copy(source, "value")
        self.call("put")

    def get_at(self, array, offset, dest):
        self.e.wset(self.g.word("index"), offset)
        self.e.set("array", list(self.g.arrays).index(array))
        self.call("get")
        self.e.copy("value", dest)

    def put_at(self, array, offset, source):
        self.e.wset(self.g.word("index"), offset)
        self.e.set("array", list(self.g.arrays).index(array))
        self.e.copy(source, "value")
        self.call("put")


def cursor(e):
    e.copy("x", 1)
    e.copy("y", 2)
    e.hw(8)


def new_line(e):
    e.set("x", 3)
    e.add("y")
    with e.temp() as flag:
        e.eq("y", 22, flag)
        def scroll():
            for cell, value in [(1,6),(2,3),(3,21),(4,76),(5,241),(6,1)]:
                e.set(cell, value)
            e.hw(9)
            e.set("y", 21)
        e.once(flag, scroll)
    cursor(e)


def emit_char(e, source=None, constant=None):
    if constant in (10, 13):
        new_line(e)
        return
    e.copy("x", 1)
    e.copy("y", 2)
    if constant is None:
        e.copy(source, 3)
    else:
        e.set(3, constant)
    e.set(4, 241)
    e.hw(7)
    e.add("x")
    with e.temp() as flag:
        e.eq("x", 77, flag)
        e.once(flag, lambda: new_line(e))
    cursor(e)


def literal(e, text):
    for char in text:
        emit_char(e, constant=ord(char))


def draw_text(e, x, y, color, text):
    e.set(1, x)
    e.set(2, y)
    e.set(4, color)
    for char in text:
        e.set(3, ord(char))
        e.hw(7)
        e.add(1)


def status(e, text):
    e.set(1, 0)
    e.set(2, 24)
    e.set(3, 32)
    e.set(4, 31)
    with e.temp() as count:
        e.set(count, 80)
        e.loop(count, lambda: (e.hw(7), e.add(1), e.add(count, -1)))
    draw_text(e, 2, 24, 31, text)


def packet(e, operation, values=None):
    """Serialize raw hardware registers from any BF tape position."""
    values = dict(values or {})
    values[0] = operation
    with e.temp() as constant:
        for register in range(16):
            value = values.get(register,0)
            if isinstance(value,tuple):
                e.go(value[1])
            else:
                e.set(constant,value)
            e.raw(".")


def file_walk(e, count, carry, trail, body):
    """Sequential BF traversal; transport local state and return it by trails.

    Scratch lives only in the metadata of future records, never their data.
    This removes a random-access lookup for every checksum/viewer byte.
    """
    g = e.g
    base = g.arrays["file"][0]
    e.copy(count[0],base); e.copy(count[1],base+1)
    for field,source in carry.items(): e.copy(source,base+field)
    saved = g.free
    g.free = [i for i in range(16,80) if i % STRIDE != 4]
    local = Emitter(g)
    local.wnz((0,1),6); local.go(6); local.raw("["); local.clear(6)
    local.wdec((0,1)); body(local); local.clear(6)
    for field in (0,1,*carry): local.move(field,field+STRIDE)
    local.set(trail,1)
    local.wnz((8,9),14); local.go(14); local.raw("]"); local.p = 6
    local.go(trail-STRIDE); local.raw("["); local.clear(trail-STRIDE)
    for field in carry: local.move(field,field-STRIDE)
    local.go(trail-STRIDE*2); local.raw("]"); local.p = trail-STRIDE
    local.go(0)
    g.free = saved
    e.go(base); e.raw(local.result()); e.p = base
    for field,dest in carry.items():
        e.clear(dest); e.move(base+field,dest)


def local_status(e,text):
    for x in range(80):
        char = ord(text[x-2]) if 2 <= x < len(text)+2 else 32
        packet(e,7,{1:x,2:24,3:char,4:31})


def local_newline(e):
    e.set(2,3); e.add(3)
    with e.temp() as flag:
        e.eq(3,22,flag)
        e.once(flag,lambda:(packet(e,9,{1:6,2:3,3:21,4:76,5:241,6:1}),e.set(3,21)))


def local_print(e,paging=False):
    # x/y are fields 2/3, the immutable source byte is field 4, CR state is 5.
    with e.temp(3) as (handled,flag,other):
        e.clear(handled)
        e.eq(4,13,flag)
        e.once(flag,lambda:(local_newline(e),e.set(5,1),e.set(handled,1)))
        e.eq(4,10,flag)
        def lf():
            e.otherwise(5,lambda:None,lambda:local_newline(e))
            e.clear(5); e.set(handled,1)
        e.once(flag,lf)
        e.zero(handled,flag)
        def other_char():
            e.clear(5); e.eq(4,8,other)
            def back():
                with e.temp() as three:
                    e.set(three,3); e.less(three,2,flag)
                    e.once(flag,lambda:(e.add(2,-1),packet(e,7,{1:("cell",2),2:("cell",3),3:32,4:241})))
                e.set(handled,1)
            e.once(other,back)
            with e.temp() as threshold:
                e.set(threshold,32); e.less(4,threshold,other)
                e.once(other,lambda:e.set(handled,1))
            e.zero(handled,other)
            def glyph():
                packet(e,7,{1:("cell",2),2:("cell",3),3:("cell",4),4:241})
                e.add(2); e.eq(2,77,flag); e.once(flag,lambda:local_newline(e))
            e.once(other,glyph)
        e.once(flag,other_char)
    if paging:
        with e.temp(4) as (flag,col,wait,key):
            e.eq(3,21,flag); e.eq(2,3,col)
            e.otherwise(col,lambda:None,lambda:e.clear(flag))
            def page():
                local_status(e," VIEWER | Space / Enter: next page   Esc: return to shell")
                packet(e,8,{1:("cell",2),2:("cell",3)})
                e.set(wait,1)
                def read():
                    packet(e,1); e.go(key); e.raw(",")
                    for char in (32,13,27):
                        e.eq(key,char,col); e.once(col,lambda:e.clear(wait))
                    e.eq(key,27,col)
                    e.once(col,lambda:(e.clear(0),e.clear(1),e.set(5,128)))
                e.loop(wait,read)
                e.eq(5,128,col)
                e.otherwise(col,lambda:None,lambda:(packet(e,9,{1:6,2:3,3:21,4:76,5:241,6:0}),e.set(2,3),e.set(3,6)))
            e.once(flag,page)


def build_memory(g):
    for operation, write in [("get", False), ("put", True)]:
        b = g.routine(operation)
        for number, (name, (base, size)) in enumerate(g.arrays.items()):
            # Stack and control flags are accessed directly by the dispatcher.
            if name in ("flags", "retlo", "rethi"):
                continue
            b.e.eq("array", number, "mem_match")
            def handle(branch, name=name):
                branch.e.array_access(name, g.word("index"), g.var("value"), write)
                branch.ret()
            b.if_("mem_match", handle)
        b.ret()


def build_ui(g):
    b = g.routine("draw")
    b.e.hw(6)
    b.e.set(1, 0)
    b.e.set(2, 0)
    b.e.set(3, 32)
    b.e.set(4, 241)
    with b.e.temp(2) as (rows, cols):
        b.e.set(rows, 25)
        def row():
            b.e.set(1, 0)
            b.e.set(cols, 80)
            b.e.loop(cols, lambda: (b.e.hw(7), b.e.add(1), b.e.add(cols, -1)))
            b.e.add(2)
            b.e.add(rows, -1)
        b.e.loop(rows, row)
    for y in (0, 24):
        b.e.set(1, 0)
        b.e.set(2, y)
        b.e.set(3, 32)
        b.e.set(4, 31)
        with b.e.temp() as count:
            b.e.set(count, 80)
            b.e.loop(count, lambda: (b.e.hw(7), b.e.add(1), b.e.add(count, -1)))
    draw_text(b.e, 2, 0, 31, "BFOS / Raw Brainfuck Desktop")
    draw_text(b.e, 2, 1, 241, "Shell, files, editor, arithmetic and programs: all BF")
    draw_text(b.e, 3, 3, 240, " help   ls   edit NAME   cat NAME   run NAME   time   calc ")
    for y, left, right in [(4,201,187),(22,200,188)]:
        b.e.set(1,2); b.e.set(2,y); b.e.set(3,left); b.e.set(4,241); b.e.hw(7)
        b.e.add(1); b.e.set(3,205)
        with b.e.temp() as count:
            b.e.set(count,74)
            b.e.loop(count, lambda: (b.e.hw(7), b.e.add(1), b.e.add(count,-1)))
        b.e.set(3,right); b.e.hw(7)
    b.e.set(2,5); b.e.set(3,186)
    with b.e.temp() as count:
        b.e.set(count,17)
        def side():
            b.e.set(1,2); b.e.hw(7); b.e.set(1,77); b.e.hw(7)
            b.e.add(2); b.e.add(count,-1)
        b.e.loop(count,side)
    draw_text(b.e,5,4,241," COMMAND WORKSPACE ")
    status(b.e, " READY | Enter: run  Up: recall  F1: help | BFOS v0.4 RAW")
    b.e.set("x",3); b.e.set("y",6); b.e.clear("last_cr"); cursor(b.e)
    b.ret()


def build_readline(g):
    b = g.routine("readline")
    b.e.array_clear("cmd")
    b.e.clear("line_len")
    b.e.set("reading",1)
    def read(a):
        a.e.hw(1)
        a.e.copy(1,"key"); a.e.copy(2,"scan"); a.e.clear("key_handled")
        a.e.eq("key",13,"is_enter")
        a.e.once("is_enter",lambda: (a.e.clear("reading"),a.e.set("key_handled",1)))
        a.e.eq("key",8,"is_back")
        def back(c):
            c.e.once("line_len",lambda: c.e.set("rl_back",1))
            def erase(d):
                d.e.add("line_len",-1)
                d.e.copy("line_len",g.word("rl_index")[0]); d.e.clear(g.word("rl_index")[1])
                d.e.clear("zero"); d.put("cmd",g.word("rl_index"),"zero")
                d.e.set("char",8); d.call("print")
            c.if_("line_len",erase)
            c.e.set("key_handled",1)
        a.if_("is_back",back)
        a.e.eq("key",27,"is_escape")
        def erase_line(c):
            def erase(d):
                d.e.set("char",8); d.call("print"); d.e.add("line_len",-1)
            c.while_(lambda e,f:e.nonzero("line_len",f),erase)
            c.e.array_clear("cmd"); c.e.set("key_handled",1)
        a.if_("is_escape",erase_line)
        a.e.zero("key","extended")
        def extended(c):
            c.e.eq("scan",72,"up"); c.e.eq("scan",59,"f1")
            c.e.copy("up","recall"); c.e.once("f1",lambda:c.e.set("recall",1))
            def recall(d):
                d.while_(lambda e,f:e.nonzero("line_len",f),
                         lambda z:(z.e.set("char",8),z.call("print"),z.e.add("line_len",-1)))
                d.e.array_clear("cmd")
                def help_line(z):
                    for offset,ch in enumerate("help"):
                        z.e.set("rl_value",ord(ch)); z.put_at("cmd",offset,"rl_value")
                    z.e.set("line_len",4)
                def history(z):
                    z.e.wset(g.word("rl_index"),0)
                    def copy(q):
                        q.get("history",g.word("rl_index"),"rl_value")
                        q.put("cmd",g.word("rl_index"),"rl_value")
                        q.e.winc(g.word("rl_index"))
                    z.while_(lambda e,f:(e.set("limit",64),e.less(g.word("rl_index")[0],"limit",f)),copy)
                    z.e.copy("history_len","line_len")
                d.if_("f1",help_line,history)
                d.e.wset(g.word("rl_index"),0)
                def echo(z):
                    z.get("cmd",g.word("rl_index"),"char"); z.call("print")
                    z.e.winc(g.word("rl_index"))
                d.while_(lambda e,f:e.less(g.word("rl_index")[0],"line_len",f),echo)
            c.if_("recall",recall)
            c.e.set("key_handled",1)
        a.if_("extended",extended)
        a.e.zero("key_handled","normal_key")
        def normal(c):
            c.e.set("low",32); c.e.less("key","low","bad_key")
            c.e.set("high",126); c.e.less("high","key","bad_high")
            c.e.once("bad_high",lambda:c.e.set("bad_key",1))
            c.e.eq("line_len",63,"line_full")
            c.e.once("line_full",lambda:c.e.set("bad_key",1))
            c.e.zero("bad_key","good_key")
            def accept(d):
                d.e.copy("line_len",g.word("rl_index")[0]); d.e.clear(g.word("rl_index")[1])
                d.put("cmd",g.word("rl_index"),"key")
                d.e.add("line_len"); d.e.copy("key","char"); d.call("print")
            c.if_("good_key",accept)
        a.if_("normal_key",normal)
    b.while_(lambda e,f:e.nonzero("reading",f),read)
    b.msg("\r")
    # Preserve history only for nonempty commands.
    def save_history(a):
        a.e.wset(g.word("rl_index"),0)
        def copy(c):
            c.get("cmd",g.word("rl_index"),"rl_value")
            c.put("history",g.word("rl_index"),"rl_value")
            c.e.winc(g.word("rl_index"))
        a.while_(lambda e,f:(e.set("limit",64),e.less(g.word("rl_index")[0],"limit",f)),copy)
        a.e.copy("line_len","history_len")
    b.if_("line_len",save_history)
    b.ret()


def build_decimal(g):
    # Repeated 16-bit division by ten, entirely in BF. Five digits suffice.
    b = g.routine("decimal")
    b.e.wcopy(g.word("number"),g.word("dec_value"))
    b.e.clear("dec_started")
    for divisor in (10000,1000,100,10,1):
        b.e.wset(g.word("dec_divisor"),divisor)
        b.e.clear("dec_digit")
        def subtract(a):
            a.e.wsub(g.word("dec_value"),g.word("dec_divisor")); a.e.add("dec_digit")
        b.while_(lambda e,f:(e.wless(g.word("dec_value"),g.word("dec_divisor"),"dec_less"),e.zero("dec_less",f)),subtract)
        b.e.once("dec_digit",lambda:b.e.set("dec_started",1))
        if divisor == 1:
            b.e.set("dec_started",1)
        b.e.once("dec_started",lambda:(b.e.copy("dec_digit","dec_char"),b.e.add("dec_char",48),emit_char(b.e,source="dec_char")))
    b.ret()


def build_shell(g):
    b = g.routine("main")
    b.call("draw")
    b.msg("BFOS v0.4 - raw Brainfuck workstation\rType help. Files persist inside the boot image.\r")
    def command(a):
        a.msg("BFOS> "); a.call("readline")
        a.e.clear("claimed")
        for index in range(8):
            a.get_at("cmd",index,f"token{index}")
        commands = [("help",0),("version",0),("about",0),("echo",2),("clear",0),
                    ("halt",0),("reboot",0),("ls",0),("cat",1),("edit",1),("rm",1),
                    ("cp",1),("mv",1),("run",1),("mem",0),("time",0),("date",0),("calc",1)]
        for name, arguments in commands:
            a.e.set("match",1)
            for index, char in enumerate(name):
                a.e.eq(f"token{index}",ord(char),"matches_char")
                a.e.otherwise("matches_char",lambda:None,lambda:a.e.clear("match"))
            a.e.eq(f"token{len(name)}",0,"terminator")
            if arguments:
                a.e.eq(f"token{len(name)}",32,"space")
                a.e.once("space",lambda:a.e.set("terminator",1))
            a.e.otherwise("terminator",lambda:None,lambda:a.e.clear("match"))
            a.e.once("claimed",lambda:a.e.clear("match"))
            def run(c,name=name):
                c.e.set("claimed",1); c.e.wset(g.word("arg"),len(name)+1)
                c.call("cmd_"+name)
            a.if_("match",run)
        a.e.zero("claimed","unclaimed")
        a.e.zero("line_len","empty")
        a.e.once("empty",lambda:a.e.clear("unclaimed"))
        a.e.once("unclaimed",lambda:literal(a.e,"Unknown command. Type help.\r"))
    b.while_(lambda e,f:e.nonzero("alive",f),command)
    b.ret()

    messages = {
        "help": "FILES: ls | cat NAME | edit NAME | rm NAME | cp SRC DEST\r"
                "       mv SRC DEST; cat: Space next page, Esc stop\r"
                "PROGRAMS: run NAME (Brainfuck; Esc cancels)\r"
                "TOOLS: calc A+B | time | date | mem | echo TEXT\r"
                "SYSTEM: help | version | about | clear | reboot | halt\r"
                "Up: recall. F1: help. EDITOR: Ctrl-S save, Esc exit.\r"
                "16 files, 2047 bytes each; lowercase names <=15 chars.\r",
        "version": "BFOS v0.4 / eight-instruction raw BF\r",
        "about": "Brainfuck OS: shell, desktop, filesystem, editor, math\rand BF interpreter are BF. Assembly only boots / hosts I/O.\r",
    }
    for name,text in messages.items():
        b = g.routine("cmd_"+name); b.msg(text); b.ret()
    b = g.routine("cmd_clear"); b.call("draw"); b.ret()
    b = g.routine("cmd_halt"); b.msg("Halted.\r"); status(b.e," HALTED | Restart the machine to return to BFOS."); b.e.hw(13); b.finish()
    b = g.routine("cmd_reboot"); b.e.hw(12); b.finish()
    b = g.routine("cmd_echo")
    b.e.wcopy(g.word("arg"),g.word("echo_i"))
    def echo(a):
        a.get("cmd",g.word("echo_i"),"char"); a.call("print"); a.e.winc(g.word("echo_i"))
    b.while_(lambda e,f:e.less(g.word("echo_i")[0],"line_len",f),echo)
    b.msg("\r"); b.ret()
    b = g.routine("cmd_mem"); b.e.hw(11)
    b.e.copy(1,g.word("number")[0]); b.e.copy(2,g.word("number")[1]); b.call("decimal")
    b.msg(" KiB conventional RAM; user BF tape: 4096 bytes.\r"); b.ret()
    for name,operation in (("time",2),("date",4)):
        b = g.routine("cmd_"+name); b.e.set(1,operation); b.e.hw(10)
        for index in range(3,7): b.e.copy(index,f"rtc{index}")
        b.e.copy(15,"rtc_error")
        def error(a): a.msg("RTC unavailable.\r")
        def show(a,name=name):
            for index in ([3,4,5] if name=="time" else [3,4,5,6]):
                a.e.copy(f"rtc{index}","bcd"); a.call("bcd")
                if name=="time" and index!=5: a.msg(":")
                if name=="date" and index in (4,5): a.msg("-")
            a.msg("\r")
        b.if_("rtc_error",error,show); b.ret()
    b = g.routine("bcd")
    b.e.copy("bcd","bcd_low"); b.e.clear("bcd_high")
    b.e.set("sixteen",16)
    b.e.less("bcd_low","sixteen","bcd_done"); b.e.zero("bcd_done","bcd_more")
    def consume():
        b.e.add("bcd_low",-16); b.e.add("bcd_high")
        b.e.less("bcd_low","sixteen","bcd_done"); b.e.zero("bcd_done","bcd_more")
    b.e.loop("bcd_more",consume)
    b.e.add("bcd_high",48); b.e.add("bcd_low",48)
    emit_char(b.e,source="bcd_high"); emit_char(b.e,source="bcd_low"); b.ret()

def build_print(g):
    b = g.routine("print")
    b.e.clear("printed")
    b.e.eq("char",13,"is_cr")
    def cr(a):
        new_line(a.e); a.e.set("last_cr",1); a.e.set("printed",1)
    b.if_("is_cr", cr)
    b.e.eq("char",10,"is_lf")
    def lf(a):
        a.e.zero("last_cr","not_cr")
        a.if_("not_cr", lambda c: new_line(c.e))
        a.e.clear("last_cr"); a.e.set("printed",1)
    b.if_("is_lf",lf)
    b.e.zero("printed","not_printed")
    def other(a):
        a.e.clear("last_cr")
        a.e.eq("char",12,"is_clear")
        a.if_("is_clear", lambda c: (c.call("draw"),c.e.set("printed",1)))
        a.e.eq("char",8,"is_back")
        def back(c):
            c.e.set("three",3); c.e.less("three","x","can_back")
            def erase(d):
                d.e.add("x",-1); d.e.copy("x",1); d.e.copy("y",2)
                d.e.set(3,32); d.e.set(4,241); d.e.hw(7); cursor(d.e)
            c.if_("can_back",erase); c.e.set("printed",1)
        a.if_("is_back",back)
        a.e.set("thirtytwo",32); a.e.less("char","thirtytwo","control")
        a.e.once("control",lambda: a.e.set("printed",1))
        a.e.zero("printed","not_printed")
        a.if_("not_printed",lambda c: emit_char(c.e,source="char"))
    b.if_("not_printed",other)
    b.ret()


def build_files(g):
    name_error = "Name: 1..15 lowercase letters, digits, dot, underscore or hyphen.\r"
    b = g.routine("parse_name")
    b.e.set("parse_ok",1); b.e.clear("name_len")
    b.e.eq("parse_array",list(g.arrays).index("name"),"source_name")
    b.e.otherwise("source_name",lambda:b.e.array_clear("name"),lambda:b.e.array_clear("dest"))
    b.e.set("skip_space",1)
    def skip(a):
        a.e.less(g.word("arg")[0],"line_len","arg_left")
        def fetch(c):
            c.get("cmd",g.word("arg"),"parse_char")
            c.e.eq("parse_char",32,"parse_space")
            c.e.otherwise("parse_space",lambda:c.e.winc(g.word("arg")),lambda:c.e.clear("skip_space"))
        a.if_("arg_left",fetch,lambda c:c.e.clear("skip_space"))
    b.while_(lambda e,f:e.nonzero("skip_space",f),skip)
    b.e.set("parse_running",1)
    def parse(a):
        a.e.less(g.word("arg")[0],"line_len","arg_left")
        def fetch(c):
            c.get("cmd",g.word("arg"),"parse_char")
            c.e.eq("parse_char",32,"parse_space")
            def accept(d):
                d.e.clear("allowed")
                for low,high in ((97,122),(48,57)):
                    d.e.set("range_low",low); d.e.set("range_high",high)
                    d.e.less("parse_char","range_low","below")
                    d.e.less("range_high","parse_char","above")
                    d.e.copy("below","outside"); d.e.once("above",lambda:d.e.set("outside",1))
                    d.e.zero("outside","inside"); d.e.once("inside",lambda:d.e.set("allowed",1))
                for ch in "._-":
                    d.e.eq("parse_char",ord(ch),"punct"); d.e.once("punct",lambda:d.e.set("allowed",1))
                d.e.eq("name_len",15,"name_full"); d.e.once("name_full",lambda:d.e.clear("allowed"))
                def store(z):
                    z.e.copy("parse_array","array")
                    z.e.copy("name_len",g.word("index")[0]); z.e.clear(g.word("index")[1])
                    z.e.copy("parse_char","value"); z.call("put")
                    z.e.add("name_len"); z.e.winc(g.word("arg"))
                d.if_("allowed",store,lambda z:(z.e.clear("parse_ok"),z.e.clear("parse_running")))
            c.if_("parse_space",lambda d:d.e.clear("parse_running"),accept)
        a.if_("arg_left",fetch,lambda c:c.e.clear("parse_running"))
    b.while_(lambda e,f:e.nonzero("parse_running",f),parse)
    b.e.zero("name_len","name_empty"); b.e.once("name_empty",lambda:b.e.clear("parse_ok"))
    b.e.zero("parse_ok","bad_name"); b.e.once("bad_name",lambda:literal(b.e,name_error)); b.ret()
    b = g.routine("single_name")
    b.e.set("parse_array",list(g.arrays).index("name")); b.call("parse_name")
    # Only whitespace may follow a single filename.
    def trailing(a):
        a.get("cmd",g.word("arg"),"parse_char")
        a.e.eq("parse_char",32,"parse_space")
        a.e.otherwise("parse_space",lambda:None,lambda:a.e.clear("parse_ok"))
        a.e.winc(g.word("arg"))
    b.while_(lambda e,f:e.less(g.word("arg")[0],"line_len",f),trailing)
    b.ret()
    b = g.routine("two_names")
    b.e.set("parse_array",list(g.arrays).index("name")); b.call("parse_name")
    def second(a):
        a.e.set("parse_array",list(g.arrays).index("dest")); a.call("parse_name")
        a.while_(lambda e,f:e.less(g.word("arg")[0],"line_len",f),trailing)
    b.if_("parse_ok",second); b.ret()

    # These routines transfer exactly one raw sector; FS choices stay in BF.
    for write in (False,True):
        b = g.routine("write_sector" if write else "read_sector")
        b.e.copy(g.word("lba")[0],1); b.e.copy(g.word("lba")[1],2)
        b.e.set(3,4); b.e.set(4,0); b.e.set(5,255) # embedded volume at LBA 0x40000
        b.e.hw(5 if write else 4)
        if not write:
            b.e.once(15,lambda:b.e.clear("fs_ok"))
        def transfer():
            b.e.clear("rw_matched")
            for name, pages in (("dir",(0,)),("file",(0,512,1024,1536))):
                b.e.eq("rw_array",list(g.arrays).index(name),"rw_type")
                for page in pages:
                    b.e.eq(g.word("rw_start")[1],page//256,"rw_page")
                    b.e.otherwise("rw_type",lambda:None,lambda:b.e.clear("rw_page"))
                    def stream(name=name,page=page):
                        # Sequential BF pointer motion, not 512 random lookups.
                        # The driver sees an unstructured stream of sector bytes.
                        base = g.arrays[name][0] + page*STRIDE + 4
                        for i in range(512):
                            b.e.go(base+i*STRIDE); b.e.raw("." if write else ",")
                        b.e.set("rw_matched",1)
                    b.e.once("rw_page",stream)
            b.e.otherwise("rw_matched",lambda:None,lambda:b.e.clear("fs_ok"))
            if write: b.e.once(15,lambda:b.e.clear("fs_ok"))
        b.e.once("fs_ok",transfer); b.ret()

    b = g.routine("dir_get")
    b.e.wcopy(g.word("entry"),g.word("index"))
    with b.e.temp() as count:
        b.e.copy("offset",count)
        b.e.loop(count,lambda:(b.e.add(count,-1),b.e.winc(g.word("index"))))
    b.e.set("array",list(g.arrays).index("dir")); b.call("get"); b.e.copy("value","meta"); b.ret()
    b = g.routine("dir_put")
    b.e.wcopy(g.word("entry"),g.word("index"))
    with b.e.temp() as count:
        b.e.copy("offset",count)
        b.e.loop(count,lambda:(b.e.add(count,-1),b.e.winc(g.word("index"))))
    b.e.set("array",list(g.arrays).index("dir")); b.e.copy("meta","value"); b.call("put"); b.ret()

    b = g.routine("fs_load")
    b.e.set("fs_ok",1); b.e.wset(g.word("lba"),0); b.e.wset(g.word("rw_start"),0)
    b.e.set("rw_array",list(g.arrays).index("dir")); b.call("read_sector")
    for offset,ch in enumerate(b"BFOSFS1\0"):
        b.get_at("dir",offset,"magic"); b.e.eq("magic",ch,"good_magic")
        b.e.otherwise("good_magic",lambda:None,lambda:b.e.clear("fs_ok"))
    b.e.clear("validate_slot"); b.e.wset(g.word("entry"),16)
    def validate(a):
        a.e.set("offset",18); a.call("dir_get")
        def used(c):
            c.e.eq("meta",1,"valid_used")
            c.e.otherwise("valid_used",lambda:None,lambda:c.e.clear("fs_ok"))
            c.e.set("offset",19); c.call("dir_get")
            c.e.set("two",2); c.e.less("meta","two","valid_bank")
            c.e.otherwise("valid_bank",lambda:None,lambda:c.e.clear("fs_ok"))
            c.e.set("offset",17); c.call("dir_get")
            c.e.set("eight",8); c.e.less("meta","eight","valid_length")
            c.e.otherwise("valid_length",lambda:None,lambda:c.e.clear("fs_ok"))
            c.e.set("offset",0); c.call("dir_get")
            c.e.otherwise("meta",lambda:None,lambda:c.e.clear("fs_ok"))
            c.e.set("offset",15); c.call("dir_get")
            c.e.once("meta",lambda:c.e.clear("fs_ok"))
        a.if_("meta",used)
        a.e.add("validate_slot");
        with a.e.temp() as count:
            a.e.set(count,24); a.e.loop(count,lambda:(a.e.add(count,-1),a.e.winc(g.word("entry"))))
    b.while_(lambda e,f:(e.set("sixteen",16),e.less("validate_slot","sixteen",f),
                          e.otherwise("fs_ok",lambda:None,lambda:e.clear(f))),validate)
    b.e.zero("fs_ok","bad_volume")
    b.e.once("bad_volume",lambda:literal(b.e,"Data disk missing or invalid. Volume not modified.\r")); b.ret()
    b = g.routine("commit")
    b.e.wset(g.word("lba"),0); b.e.wset(g.word("rw_start"),0)
    b.e.set("rw_array",list(g.arrays).index("dir")); b.call("write_sector"); b.ret()

    b = g.routine("find")
    b.e.clear("found"); b.e.clear("find_slot"); b.e.wset(g.word("entry"),16)
    def find(a):
        a.e.set("offset",18); a.call("dir_get"); a.e.eq("meta",1,"used")
        def compare(c):
            c.e.set("file_match",1); c.e.wset(g.word("name_i"),0)
            def char(d):
                d.get("name",g.word("name_i"),"name_char")
                d.e.copy(g.word("name_i")[0],"offset"); d.call("dir_get")
                d.e.equal("meta","name_char","equal_char")
                d.e.otherwise("equal_char",lambda:None,lambda:d.e.clear("file_match"))
                d.e.winc(g.word("name_i"))
            c.while_(lambda e,f:(e.set("sixteen",16),e.less(g.word("name_i")[0],"sixteen",f)),char)
            def capture(d):
                d.e.set("found",1); d.e.copy("find_slot","file_slot"); d.e.wcopy(g.word("entry"),g.word("file_entry"))
            c.if_("file_match",capture)
        a.if_("used",compare)
        a.e.add("find_slot")
        with a.e.temp() as count:
            a.e.set(count,24); a.e.loop(count,lambda:(a.e.add(count,-1),a.e.winc(g.word("entry"))))
    b.while_(lambda e,f:(e.set("sixteen",16),e.less("find_slot","sixteen",f),
                          e.once("found",lambda:e.clear(f))),find)
    b.e.once("found",lambda:b.e.wcopy(g.word("file_entry"),g.word("entry"))); b.ret()
    b = g.routine("choose")
    b.call("find")
    def choose(a):
        a.e.clear("find_slot"); a.e.wset(g.word("entry"),16)
        def free(c):
            c.e.set("offset",18); c.call("dir_get"); c.e.zero("meta","free")
            c.e.once("free",lambda:(c.e.set("found",1),c.e.copy("find_slot","file_slot"),c.e.wcopy(g.word("entry"),g.word("file_entry"))))
            c.e.add("find_slot")
            with c.e.temp() as count:
                c.e.set(count,24); c.e.loop(count,lambda:(c.e.add(count,-1),c.e.winc(g.word("entry"))))
        a.while_(lambda e,f:(e.set("sixteen",16),e.less("find_slot","sixteen",f),e.once("found",lambda:e.clear(f))),free)
        a.e.once("found",lambda:a.e.wcopy(g.word("file_entry"),g.word("entry")))
    b.if_("found",lambda a:None,choose)
    b.e.zero("found","no_slot")
    b.e.once("no_slot",lambda:(b.e.clear("fs_ok"),literal(b.e,"Directory full (16 files).\r"))); b.ret()
    b = g.routine("file_lba")
    b.e.wset(g.word("lba"),1)
    with b.e.temp() as count:
        b.e.copy("file_slot",count); b.e.loop(count,lambda:(b.e.add(count,-1),b.e.add(g.word("lba")[0],8)))
    b.e.once("bank",lambda:b.e.add(g.word("lba")[0],4)); b.ret()

    b = g.routine("checksum")
    b.e.wset(g.word("sum"),0)
    def sum_byte(e):
        e.copy(2,5); e.copy(4,7); e.move(7,2)
        e.less(2,5,6); e.once(6,lambda:e.add(3)); e.clear(5)
    file_walk(b.e,g.word("file_len"),{2:g.word("sum")[0],3:g.word("sum")[1]},5,sum_byte)
    b.ret()
    b = g.routine("read_file")
    b.call("find")
    def read(a):
        for offset,dest in [(16,g.word("file_len")[0]),(17,g.word("file_len")[1]),(19,"bank"),(20,g.word("expected_sum")[0]),(21,g.word("expected_sum")[1])]:
            a.e.set("offset",offset); a.call("dir_get"); a.e.copy("meta",dest)
        a.call("file_lba"); a.e.wset(g.word("rw_start"),0); a.e.set("rw_array",list(g.arrays).index("file"))
        for sector in range(4):
            a.call("read_sector"); a.e.winc(g.word("lba")); a.e.add(g.word("rw_start")[1],2)
        a.call("checksum"); a.e.weq(g.word("sum"),g.word("expected_sum"),"valid_sum")
        a.e.otherwise("valid_sum",lambda:None,lambda:(a.e.clear("fs_ok"),literal(a.e,"File checksum mismatch; refusing damaged content.\r")))
    b.if_("found",read,lambda a:(a.e.clear("fs_ok"),a.msg("File not found.\r")))
    b.ret()
    b = g.routine("save_file")
    b.call("fs_load")
    def save(a):
        a.call("choose")
        def data(c):
            c.e.wcopy(g.word("file_entry"),g.word("entry")); c.e.set("offset",19); c.call("dir_get")
            c.e.zero("meta","bank")
            c.call("checksum"); c.call("file_lba")
            c.e.wset(g.word("rw_start"),0); c.e.set("rw_array",list(g.arrays).index("file"))
            for sector in range(4):
                c.call("write_sector"); c.e.winc(g.word("lba")); c.e.add(g.word("rw_start")[1],2)
            def metadata(d):
                d.e.wcopy(g.word("file_entry"),g.word("entry")); d.e.clear("offset")
                def clear(z):
                    z.e.clear("meta"); z.call("dir_put"); z.e.add("offset")
                d.while_(lambda e,f:(e.set("twentyfour",24),e.less("offset","twentyfour",f)),clear)
                d.e.wset(g.word("save_name_i"),0)
                def name(z):
                    z.get("name",g.word("save_name_i"),"meta")
                    z.e.copy(g.word("save_name_i")[0],"offset"); z.call("dir_put"); z.e.winc(g.word("save_name_i"))
                d.while_(lambda e,f:(e.set("sixteen",16),e.less(g.word("save_name_i")[0],"sixteen",f)),name)
                for offset,source in [(16,g.word("file_len")[0]),(17,g.word("file_len")[1]),(18,"one"),(19,"bank"),(20,g.word("sum")[0]),(21,g.word("sum")[1])]:
                    d.e.set("one",1); d.e.set("offset",offset); d.e.copy(source,"meta"); d.call("dir_put")
                d.call("commit")
            c.if_("fs_ok",metadata)
        a.if_("fs_ok",data)
    b.if_("fs_ok",save)
    b.e.zero("fs_ok","save_failed")
    b.e.once("save_failed",lambda:literal(b.e,"Disk I/O failed. Save not confirmed.\r")); b.ret()


def build_file_commands(g):
    for paging in (False,True):
        b = g.routine("file_print_paged" if paging else "file_print_plain")
        b.e.set("view_active",1)
        file_walk(b.e,g.word("file_len"),{2:g.var("x"),3:g.var("y"),5:g.var("last_cr")},7,
                  lambda e,paging=paging:local_print(e,paging))
        b.e.eq("last_cr",128,"view_cancelled")
        b.e.once("view_cancelled",lambda:(b.e.clear("view_active"),b.e.clear("last_cr")))
        cursor(b.e)
        if paging: status(b.e," READY | Enter: run  Up: recall  F1: help | BFOS v0.4 RAW")
        b.ret()
    b = g.routine("pager")
    b.e.eq("y",21,"page_row"); b.e.eq("x",3,"page_col")
    b.e.otherwise("page_col",lambda:None,lambda:b.e.clear("page_row"))
    def page(a):
        status(a.e," VIEWER | Space / Enter: next page   Esc: return to shell")
        a.e.set("page_wait",1)
        def wait(c):
            c.e.hw(1); c.e.copy(1,"page_key")
            for key in (32,13):
                c.e.eq("page_key",key,"page_next"); c.e.once("page_next",lambda:c.e.clear("page_wait"))
            c.e.eq("page_key",27,"page_escape")
            c.e.once("page_escape",lambda:(c.e.clear("page_wait"),c.e.clear("view_active")))
        a.while_(lambda e,f:e.nonzero("page_wait",f),wait)
        a.if_("view_active",lambda c:c.call("draw"),lambda c:status(c.e," READY | Enter: run  Up: recall  F1: help | BFOS v0.4 RAW"))
    b.if_("page_row",page); b.ret()

    b = g.routine("cmd_cat"); b.call("single_name")
    def cat(a):
        a.call("fs_load")
        def loaded(c):
            c.call("read_file")
            def show(d):
                d.call("file_print_paged")
                d.msg("\r")
            c.if_("fs_ok",show)
        a.if_("fs_ok",loaded)
    b.if_("parse_ok",cat); b.ret()

    b = g.routine("cmd_ls"); b.call("fs_load")
    def list_files(a):
        a.msg("DATA:/  NAME / BYTES\r"); a.e.wset(g.word("entry"),16)
        a.e.clear("ls_slot"); a.e.clear("ls_count"); a.e.set("view_active",1)
        def entry(c):
            c.e.set("offset",18); c.call("dir_get")
            def used(d):
                d.e.add("ls_count"); d.e.clear("ls_name_i"); d.e.set("ls_name_more",1)
                def char(z):
                    z.e.copy("ls_name_i","offset"); z.call("dir_get")
                    z.e.copy("meta","char")
                    z.if_("meta",lambda q:q.call("print"),lambda q:q.e.clear("ls_name_more"))
                    z.e.add("ls_name_i")
                d.while_(lambda e,f:e.nonzero("ls_name_more",f),char)
                d.msg(" / ")
                for offset,dest in ((16,g.word("number")[0]),(17,g.word("number")[1])):
                    d.e.set("offset",offset); d.call("dir_get"); d.e.copy("meta",dest)
                d.call("decimal"); d.msg("\r"); d.call("pager")
            c.if_("meta",used)
            c.e.add("ls_slot")
            with c.e.temp() as count:
                c.e.set(count,24); c.e.loop(count,lambda:(c.e.add(count,-1),c.e.winc(g.word("entry"))))
        a.while_(lambda e,f:(e.set("sixteen",16),e.less("ls_slot","sixteen",f),e.otherwise("view_active",lambda:None,lambda:e.clear(f))),entry)
        a.e.copy("ls_count",g.word("number")[0]); a.e.clear(g.word("number")[1]); a.call("decimal")
        a.msg(" / 16 file slots used. Max 2047 bytes per file.\r")
    b.if_("fs_ok",list_files); b.ret()

    b = g.routine("cmd_rm"); b.call("single_name")
    def remove(a):
        a.call("fs_load")
        def loaded(c):
            c.call("find")
            def found(d):
                d.e.wcopy(g.word("file_entry"),g.word("entry")); d.e.set("offset",18); d.e.clear("meta"); d.call("dir_put")
                d.call("commit"); d.e.once("fs_ok",lambda:literal(d.e,"Removed.\r"))
                d.e.zero("fs_ok","rm_failed"); d.e.once("rm_failed",lambda:literal(d.e,"Disk I/O failed.\r"))
            c.if_("found",found,lambda d:d.msg("File not found.\r"))
        a.if_("fs_ok",loaded)
    b.if_("parse_ok",remove); b.ret()

    b = g.routine("dest_to_name")
    b.e.wset(g.word("dest_i"),0)
    def copy(a):
        a.get("dest",g.word("dest_i"),"dest_char"); a.put("name",g.word("dest_i"),"dest_char"); a.e.winc(g.word("dest_i"))
    b.while_(lambda e,f:(e.set("sixteen",16),e.less(g.word("dest_i")[0],"sixteen",f)),copy); b.ret()
    for move in (False,True):
        b = g.routine("cmd_mv" if move else "cmd_cp"); b.call("two_names")
        def operation(a,move=move):
            a.call("fs_load")
            def loaded(c):
                c.call("find" if move else "read_file")
                c.e.copy("found","source_found")
                if move:
                    c.e.wcopy(g.word("file_entry"),g.word("rename_entry"))
                def source(d):
                    d.call("dest_to_name"); d.call("find")
                    def available(z):
                        if move:
                            z.e.wcopy(g.word("rename_entry"),g.word("entry")); z.e.wset(g.word("rename_i"),0)
                            def name(q):
                                q.get("name",g.word("rename_i"),"meta"); q.e.copy(g.word("rename_i")[0],"offset")
                                q.call("dir_put"); q.e.winc(g.word("rename_i"))
                            z.while_(lambda e,f:(e.set("sixteen",16),e.less(g.word("rename_i")[0],"sixteen",f)),name)
                            z.call("commit")
                        else:
                            z.call("save_file")
                        z.e.once("fs_ok",lambda:literal(z.e,"Renamed.\r" if move else "Saved.\r"))
                    d.if_("found",lambda z:z.msg("Destination exists; choose a new name.\r"),available)
                c.e.otherwise("fs_ok",lambda:None,lambda:c.e.clear("source_found"))
                c.if_("source_found",source,lambda d:d.msg("File not found.\r") if move else None)
            a.if_("fs_ok",loaded)
        b.if_("parse_ok",operation); b.ret()

    b = g.routine("editor_draw")
    b.call("draw"); b.msg("EDIT / ")
    b.e.wset(g.word("edit_name_i"),0); b.e.set("edit_name_more",1)
    def name(a):
        a.get("name",g.word("edit_name_i"),"char")
        a.if_("char",lambda c:c.call("print"),lambda c:c.e.clear("edit_name_more"))
        a.e.winc(g.word("edit_name_i"))
    b.while_(lambda e,f:e.nonzero("edit_name_more",f),name)
    b.msg("\rAppend at end. Backspace removes last character.\r\r")
    b.call("file_print_plain")
    status(b.e," EDITOR | Ctrl-S: save  Esc: exit | Append / Backspace | 2047 bytes"); b.ret()
    b = g.routine("cmd_edit"); b.call("single_name")
    def edit(a):
        a.call("fs_load")
        def loaded(c):
            c.call("find")
            def new(d):
                d.call("choose"); d.e.wset(g.word("file_len"),0); d.e.array_clear("file")
            c.if_("found",lambda d:d.call("read_file"),new)
            def open(d):
                d.e.clear("dirty"); d.e.set("editing",1); d.call("editor_draw")
                def key(z):
                    z.e.hw(1); z.e.copy(1,"edit_key"); z.e.clear("edit_handled")
                    z.e.eq("edit_key",19,"edit_save")
                    def save(q):
                        q.call("save_file")
                        q.if_("fs_ok",lambda t:(t.e.clear("dirty"),status(t.e," SAVED | Ctrl-S: save  Esc: exit")),
                              lambda t:(t.call("editor_draw"),status(t.e," SAVE FAILED | Retry Ctrl-S; last save unconfirmed.")))
                        q.e.set("edit_handled",1)
                    z.if_("edit_save",save)
                    z.e.eq("edit_key",27,"edit_exit")
                    def exit(q):
                        def dirty(t):
                            status(t.e," UNSAVED CHANGES | Discard and exit? Y = discard, other key = stay")
                            t.e.hw(1); t.e.copy(1,"discard_key"); t.e.eq("discard_key",121,"discard")
                            t.e.eq("discard_key",89,"uppercase_y"); t.e.once("uppercase_y",lambda:t.e.set("discard",1))
                            t.if_("discard",lambda v:v.e.clear("editing"),lambda v:v.call("editor_draw"))
                        q.if_("dirty",dirty,lambda t:t.e.clear("editing")); q.e.set("edit_handled",1)
                    z.if_("edit_exit",exit)
                    z.e.eq("edit_key",8,"edit_back")
                    def back(q):
                        q.e.wnz(g.word("file_len"),"edit_nonempty")
                        def erase(t):
                            t.e.wdec(g.word("file_len")); t.e.clear("zero")
                            t.put("file",g.word("file_len"),"zero"); t.e.set("dirty",1); t.call("editor_draw")
                        q.if_("edit_nonempty",erase); q.e.set("edit_handled",1)
                    z.if_("edit_back",back)
                    z.e.zero("edit_handled","edit_normal")
                    def normal(q):
                        q.e.set("ascii_low",32); q.e.set("ascii_high",126)
                        q.e.less("edit_key","ascii_low","edit_low"); q.e.less("ascii_high","edit_key","edit_high")
                        q.e.copy("edit_low","edit_bad"); q.e.once("edit_high",lambda:q.e.set("edit_bad",1))
                        q.e.eq("edit_key",13,"edit_cr"); q.e.once("edit_cr",lambda:q.e.clear("edit_bad"))
                        q.e.zero("edit_bad","edit_good")
                        def append(t):
                            t.e.wset(g.word("file_limit"),2047); t.e.wless(g.word("file_len"),g.word("file_limit"),"file_room")
                            def room(v):
                                v.put("file",g.word("file_len"),"edit_key"); v.e.winc(g.word("file_len")); v.e.set("dirty",1)
                                status(v.e," UNSAVED | Ctrl-S: save  Esc: exit | Append / Backspace")
                                v.e.copy("edit_key","char"); v.call("print")
                            t.if_("file_room",room,lambda v:status(v.e," FILE FULL | 2047 bytes maximum. Backspace removes text."))
                        q.if_("edit_good",append)
                    z.if_("edit_normal",normal)
                d.while_(lambda e,f:e.nonzero("editing",f),key); d.call("draw")
            c.if_("fs_ok",open)
        a.if_("fs_ok",loaded)
    b.if_("parse_ok",edit); b.ret()


def build_runner(g):
    b = g.routine("cmd_run"); b.call("single_name")
    def run(a):
        a.call("fs_load")
        def loaded(c):
            c.call("read_file")
            def file(d):
                d.e.wset(g.word("check_pc"),0); d.e.wset(g.word("bracket_depth"),0); d.e.set("brackets_ok",1)
                def validate(z):
                    z.get("file",g.word("check_pc"),"run_op")
                    z.e.eq("run_op",91,"open_bracket")
                    z.e.once("open_bracket",lambda:z.e.winc(g.word("bracket_depth")))
                    z.e.eq("run_op",93,"close_bracket")
                    def close(q):
                        q.e.wnz(g.word("bracket_depth"),"depth_nonzero")
                        q.e.otherwise("depth_nonzero",lambda:q.e.wdec(g.word("bracket_depth")),lambda:q.e.clear("brackets_ok"))
                    z.e.once("close_bracket",lambda:close(z))
                    z.e.winc(g.word("check_pc"))
                d.while_(lambda e,f:e.wless(g.word("check_pc"),g.word("file_len"),f),validate)
                d.e.wnz(g.word("bracket_depth"),"unclosed"); d.e.once("unclosed",lambda:d.e.clear("brackets_ok"))
                def execute(z):
                    z.e.array_clear("user"); z.e.wset(g.word("pc"),0); z.e.wset(g.word("ptr"),0)
                    z.e.wset(g.word("budget"),10000); z.e.set("run_active",1); z.e.set("poll_count",16)
                    def instruction(q):
                        q.e.add("poll_count",-1); q.e.zero("poll_count","poll_now")
                        def poll(t):
                            t.e.set("poll_count",16); t.e.hw(2); t.e.copy(3,"key_ready"); t.e.copy(1,"poll_key")
                            t.e.eq("poll_key",27,"poll_escape")
                            t.e.otherwise("key_ready",lambda:None,lambda:t.e.clear("poll_escape"))
                            t.e.once("poll_escape",lambda:(t.e.hw(1),t.e.clear("run_active")))
                        q.e.once("poll_now",lambda:poll(q))
                        def step(t):
                            t.get("file",g.word("pc"),"run_op"); t.e.winc(g.word("pc"))
                            t.get("user",g.word("ptr"),"run_value"); t.e.clear("write_value")
                            for char,delta in (("+",1),("-",-1)):
                                t.e.eq("run_op",ord(char),"opcode_match")
                                t.e.once("opcode_match",lambda delta=delta:(t.e.add("run_value",delta),t.e.set("write_value",1)))
                            t.e.eq("run_op",62,"right")
                            def right():
                                t.e.winc(g.word("ptr")); t.e.eq(g.word("ptr")[1],16,"ptr_end")
                                t.e.once("ptr_end",lambda:t.e.clear(g.word("ptr")[1]))
                            t.e.once("right",right)
                            t.e.eq("run_op",60,"left")
                            def left():
                                t.e.wdec(g.word("ptr")); t.e.eq(g.word("ptr")[1],255,"ptr_start")
                                t.e.once("ptr_start",lambda:t.e.set(g.word("ptr")[1],15))
                            t.e.once("left",left)
                            t.e.eq("run_op",46,"output")
                            t.if_("output",lambda v:(v.e.copy("run_value","char"),v.call("print")))
                            t.e.eq("run_op",44,"input")
                            def read(v):
                                v.e.set("input_wait",1)
                                def key(w):
                                    w.e.hw(1); w.e.copy(1,"run_value")
                                    w.e.once("run_value",lambda:w.e.clear("input_wait"))
                                v.while_(lambda e,f:e.nonzero("input_wait",f),key)
                                v.e.eq("run_value",27,"input_escape")
                                v.e.otherwise("input_escape",lambda:v.e.clear("run_active"),lambda:v.e.set("write_value",1))
                            t.if_("input",read)
                            t.e.eq("run_op",91,"open")
                            t.e.once("run_value",lambda:t.e.clear("open"))
                            def forward(v):
                                v.e.wset(g.word("scan_depth"),1)
                                def scan(w):
                                    w.get("file",g.word("pc"),"scan_op"); w.e.winc(g.word("pc"))
                                    w.e.eq("scan_op",91,"scan_open"); w.e.eq("scan_op",93,"scan_close")
                                    w.e.once("scan_open",lambda:w.e.winc(g.word("scan_depth")))
                                    w.e.once("scan_close",lambda:w.e.wdec(g.word("scan_depth")))
                                v.while_(lambda e,f:e.wnz(g.word("scan_depth"),f),scan)
                            t.if_("open",forward)
                            t.e.eq("run_op",93,"close"); t.e.otherwise("run_value",lambda:None,lambda:t.e.clear("close"))
                            def backward(v):
                                v.e.wset(g.word("scan_depth"),1); v.e.wdec(g.word("pc"))
                                def scan(w):
                                    w.e.wdec(g.word("pc")); w.get("file",g.word("pc"),"scan_op")
                                    w.e.eq("scan_op",93,"scan_close"); w.e.eq("scan_op",91,"scan_open")
                                    w.e.once("scan_close",lambda:w.e.winc(g.word("scan_depth")))
                                    w.e.once("scan_open",lambda:w.e.wdec(g.word("scan_depth")))
                                v.while_(lambda e,f:e.wnz(g.word("scan_depth"),f),scan); v.e.winc(g.word("pc"))
                            t.if_("close",backward)
                            t.if_("write_value",lambda v:v.put("user",g.word("ptr"),"run_value"))
                        q.if_("run_active",step); q.e.wdec(g.word("budget"))
                    def more(e,f):
                        e.wless(g.word("pc"),g.word("file_len"),f)
                        e.wnz(g.word("budget"),"has_budget")
                        e.otherwise("has_budget",lambda:None,lambda:e.clear(f))
                        e.otherwise("run_active",lambda:None,lambda:e.clear(f))
                    z.while_(more,instruction)
                    def finished(q):
                        q.e.wnz(g.word("budget"),"budget_left")
                        q.if_("budget_left",lambda t:t.msg("\r[Program finished]\r"),lambda t:t.msg("\rProgram stopped: 10,000 instruction limit.\r"))
                    z.if_("run_active",finished,lambda q:q.msg("\rProgram cancelled.\r"))
                d.if_("brackets_ok",execute,lambda z:z.msg("Program rejected: unmatched brackets.\r"))
            c.if_("fs_ok",file)
        a.if_("fs_ok",loaded)
    b.if_("parse_ok",run); b.ret()


def build_calc(g):
    error = "Use calc A+B, A-B, A*B or A/B; unsigned 0..65535; no overflow.\r"
    b = g.routine("calc_spaces"); b.e.set("calc_skipping",1)
    def skip(a):
        a.e.less(g.word("arg")[0],"line_len","calc_left")
        def char(c):
            c.get("cmd",g.word("arg"),"calc_char"); c.e.eq("calc_char",32,"calc_space")
            c.e.otherwise("calc_space",lambda:c.e.winc(g.word("arg")),lambda:c.e.clear("calc_skipping"))
        a.if_("calc_left",char,lambda c:c.e.clear("calc_skipping"))
    b.while_(lambda e,f:e.nonzero("calc_skipping",f),skip); b.ret()
    b = g.routine("parse_number"); b.call("calc_spaces")
    b.e.wset(g.word("number"),0); b.e.clear("num_digits"); b.e.clear("num_overflow"); b.e.set("num_reading",1)
    def read(a):
        a.e.less(g.word("arg")[0],"line_len","num_left")
        def char(c):
            c.get("cmd",g.word("arg"),"num_char")
            c.e.set("digit_low",48); c.e.set("digit_high",57)
            c.e.less("num_char","digit_low","digit_below"); c.e.less("digit_high","num_char","digit_above")
            c.e.copy("digit_below","not_digit"); c.e.once("digit_above",lambda:c.e.set("not_digit",1))
            def digit(d):
                d.e.wcopy(g.word("number"),g.word("num_original")); d.e.wset(g.word("number"),0)
                with d.e.temp() as count:
                    d.e.set(count,10)
                    d.e.loop(count,lambda:(d.e.add(count,-1),d.e.wadd(g.word("number"),g.word("num_original"),"num_overflow")))
                d.e.add("num_char",-48)
                d.e.copy("num_char",g.word("num_digit")[0]); d.e.clear(g.word("num_digit")[1])
                d.e.wadd(g.word("number"),g.word("num_digit"),"num_overflow")
                d.e.add("num_digits"); d.e.winc(g.word("arg"))
            c.if_("not_digit",lambda d:d.e.clear("num_reading"),digit)
        a.if_("num_left",char,lambda c:c.e.clear("num_reading"))
    b.while_(lambda e,f:e.nonzero("num_reading",f),read)
    b.e.nonzero("num_digits","num_ok"); b.e.once("num_overflow",lambda:b.e.clear("num_ok")); b.ret()

    b = g.routine("cmd_calc"); b.call("parse_number")
    b.e.copy("num_ok","calc_ok"); b.e.wcopy(g.word("number"),g.word("calc_a")); b.call("calc_spaces")
    b.get("cmd",g.word("arg"),"calc_op"); b.e.winc(g.word("arg")); b.call("parse_number")
    b.e.otherwise("num_ok",lambda:None,lambda:b.e.clear("calc_ok"))
    b.e.wcopy(g.word("number"),g.word("calc_b")); b.call("calc_spaces")
    b.e.less(g.word("arg")[0],"line_len","extra_calc"); b.e.once("extra_calc",lambda:b.e.clear("calc_ok"))
    def calculate(a):
        a.e.clear("calc_matched"); a.e.clear("calc_overflow")
        for operator in "+-*/":
            a.e.eq("calc_op",ord(operator),"calc_match")
            def op(c,operator=operator):
                c.e.set("calc_matched",1)
                if operator == "+":
                    c.e.wcopy(g.word("calc_a"),g.word("number"))
                    c.e.wadd(g.word("number"),g.word("calc_b"),"calc_overflow")
                elif operator == "-":
                    c.e.wless(g.word("calc_a"),g.word("calc_b"),"subtract_underflow")
                    c.e.once("subtract_underflow",lambda:c.e.clear("calc_ok"))
                    c.e.wcopy(g.word("calc_a"),g.word("number")); c.e.wsub(g.word("number"),g.word("calc_b"))
                elif operator == "*":
                    c.e.wset(g.word("number"),0)
                    c.e.wcopy(g.word("calc_a"),g.word("factor")); c.e.wcopy(g.word("calc_b"),g.word("multiplier"))
                    c.e.wnz(g.word("multiplier"),"multiply_more")
                    def multiply():
                        c.e.halve(g.word("multiplier"),"multiply_bit")
                        c.e.once("multiply_bit",lambda:c.e.wadd(g.word("number"),g.word("factor"),"calc_overflow"))
                        c.e.wnz(g.word("multiplier"),"multiply_more")
                        def shift():
                            c.e.wcopy(g.word("factor"),g.word("shift_copy"))
                            c.e.wadd(g.word("factor"),g.word("shift_copy"),"calc_overflow")
                        c.e.once("multiply_more",shift)
                        c.e.once("calc_overflow",lambda:c.e.clear("multiply_more"))
                    c.e.loop("multiply_more",multiply)
                else:
                    c.e.wnz(g.word("calc_b"),"divisor_nonzero")
                    c.e.otherwise("divisor_nonzero",lambda:None,lambda:c.e.clear("calc_ok"))
                    c.e.wcopy(g.word("calc_a"),g.word("numerator")); c.e.wset(g.word("remainder"),0); c.e.wset(g.word("number"),0)
                    c.e.set("divide_bits",16)
                    def divide():
                        c.e.set("bit_threshold",128); c.e.less(g.word("numerator")[1],"bit_threshold","bit_zero")
                        c.e.zero("bit_zero","numerator_bit")
                        c.e.wcopy(g.word("numerator"),g.word("shift_copy")); c.e.wadd(g.word("numerator"),g.word("shift_copy"))
                        c.e.wcopy(g.word("remainder"),g.word("shift_copy")); c.e.clear("remainder_carry")
                        c.e.wadd(g.word("remainder"),g.word("shift_copy"),"remainder_carry")
                        c.e.once("numerator_bit",lambda:c.e.winc(g.word("remainder")))
                        c.e.wcopy(g.word("number"),g.word("shift_copy")); c.e.wadd(g.word("number"),g.word("shift_copy"))
                        c.e.wless(g.word("remainder"),g.word("calc_b"),"remainder_less")
                        c.e.zero("remainder_less","divide_subtract")
                        c.e.once("remainder_carry",lambda:c.e.set("divide_subtract",1))
                        c.e.once("divide_subtract",lambda:(c.e.wsub(g.word("remainder"),g.word("calc_b")),c.e.winc(g.word("number"))))
                        c.e.add("divide_bits",-1)
                    c.e.loop("divide_bits",divide)
                c.e.once("calc_overflow",lambda:c.e.clear("calc_ok"))
            a.if_("calc_match",op)
        a.e.otherwise("calc_matched",lambda:None,lambda:a.e.clear("calc_ok"))
    b.if_("calc_ok",calculate)
    b.if_("calc_ok",lambda a:(a.call("decimal"),a.msg("\r")),lambda a:a.msg(error)); b.ret()


def build_return_dispatch(g):
    b = g.routine("return_dispatch")
    bits = [g.var(f"return_bit{i}") for i in range(10)]
    b.e.copy(g.word("return_address")[0],"return_work")
    for bit in bits[:8]: b.e.halve_byte("return_work",bit)
    b.e.copy(g.word("return_address")[1],"return_work")
    for bit in bits[8:]: b.e.halve_byte("return_work",bit)
    def tree(prefix,depth):
        if depth < 0:
            b.e.flag(prefix)
            return
        b.e.otherwise(bits[depth],lambda:tree(prefix|(1<<depth),depth-1),lambda:tree(prefix,depth-1))
    tree(0,9)
    b.finish()


def write_kernel(g):
    init = Emitter(g)
    init.set("alive",1)
    init.flag(g.entry("main"))
    init.go("alive")
    init.raw("[")
    parts = [init.result()]
    mapping = []
    offset = len(parts[0])
    for number, block in enumerate(g.blocks):
        flag = g.arrays["flags"][0] + STRIDE*number + 4
        e = Emitter(g)
        e.p = g.var("alive") if number == 0 else g.arrays["flags"][0] + STRIDE*(number-1)+4
        e.go(flag); e.raw("[[-]"); e.go(0); e.raw(block["code"])
        # Each block's actual terminal pointer is recorded by finish().
        e.p = block.get("end",0)
        e.go(flag); e.raw("]")
        code = e.result()
        mapping.append({"id":number,"name":block["name"],"offset":offset,"bytes":len(code)})
        offset += len(code)
        parts.append(code)
    e = Emitter(g)
    e.p = g.arrays["flags"][0]+STRIDE*(len(g.blocks)-1)+4
    e.go("alive"); e.raw("]")
    parts.append(e.result())
    code = "".join(parts)
    assert set(code) <= set("><+-.,[]")
    depth = 0
    for ch in code:
        if ch == "[": depth += 1
        elif ch == "]": depth -= 1
        assert depth >= 0
    assert depth == 0
    (ROOT/"kernel.bf").write_text(code,encoding="ascii")
    (ROOT/"kernel.map.json").write_text(json.dumps({"bytes":len(code),"cells":g.next_array,
        "variables":g.variables,"arrays":g.arrays,"blocks":mapping},indent=2),encoding="utf-8")
    (ROOT/"kernel_config.inc").write_text(f"KERNEL_BYTES equ {len(code)}\nKERNEL_SECTORS equ {(len(code)+32767)//32768*64}\n",encoding="ascii")
    print(f"RAW BF: {len(code):,} instructions; {len(g.blocks)} states; {g.next_var} globals; {g.next_array} tape cells")


if __name__ == "__main__":
    g = Generator()
    build_memory(g)
    build_ui(g)
    build_readline(g)
    build_decimal(g)
    build_shell(g)
    build_print(g)
    build_files(g)
    build_file_commands(g)
    build_runner(g)
    build_calc(g)
    build_return_dispatch(g)
    write_kernel(g)
