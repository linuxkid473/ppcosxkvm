#!/usr/bin/env python3
"""Drive a running VM from the host (for scripting and testing).

Start the VM with `./ppcosx run --monitor` (or `install --monitor`), then:

    tools/vmctl.py cmd '<HMP command>'     e.g. cmd 'info pci'
    tools/vmctl.py type 'text'             type into the guest
    tools/vmctl.py key meta_l-q            one key combo (HMP sendkey syntax)
    tools/vmctl.py shot out.png            screenshot (out.ppm: QEMU's raw dump)
    tools/vmctl.py click X Y [right]       click / dclick / move / drag x0 y0 x1 y1

Coordinates are guest pixels; the screen size is read from the VM
(VMCTL_RES=WxH overrides it). An installed Tiger scales tablet input about
the centre by 1.1775; the installer DVD does not, so set VMCTL_SCALE=1 while
driving the installer.
"""
import socket, sys, time, json, os, re, struct, zlib

def hmp(cmd, wait=0.3):
    s = socket.create_connection(('127.0.0.1', 4444))
    s.settimeout(2)
    buf = b''
    try:
        while b'(qemu)' not in buf:
            buf += s.recv(4096)
    except socket.timeout:
        pass
    s.sendall(cmd.encode() + b'\n')
    time.sleep(wait)
    out = b''
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            out += d
            if out.rstrip().endswith(b'(qemu)'):
                break
    except socket.timeout:
        pass
    s.close()
    return out.decode(errors='replace')

KEYS = {' ': 'spc', '\n': 'ret', '\t': 'tab', '-': 'minus', '=': 'equal',
        '[': 'bracket_left', ']': 'bracket_right', '\\': 'backslash',
        ';': 'semicolon', "'": 'apostrophe', '`': 'grave_accent', ',': 'comma',
        '.': 'dot', '/': 'slash'}
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7',
           '*': '8', '(': '9', ')': '0', '_': 'minus', '+': 'equal', '{': 'bracket_left',
           '}': 'bracket_right', '|': 'backslash', ':': 'semicolon', '"': 'apostrophe',
           '~': 'grave_accent', '<': 'comma', '>': 'dot', '?': 'slash'}

def keyname(c):
    if c.isalpha():
        return ('shift-' if c.isupper() else '') + c.lower()
    if c.isdigit():
        return c
    if c in KEYS:
        return KEYS[c]
    if c in SHIFTED:
        k = SHIFTED[c]
        return 'shift-' + KEYS.get(k, k)
    raise ValueError(c)

def qmp(cmds):
    s = socket.create_connection(('127.0.0.1', 4445))
    f = s.makefile('rw')
    f.readline()
    f.write(json.dumps({'execute': 'qmp_capabilities'}) + '\n'); f.flush(); f.readline()
    res = []
    for c in cmds:
        f.write(json.dumps(c) + '\n'); f.flush()
        while True:
            l = json.loads(f.readline())
            if 'return' in l or 'error' in l:
                res.append(l); break
    s.close()
    return res

def screen_size():
    if os.environ.get('VMCTL_RES'):
        return tuple(map(int, os.environ['VMCTL_RES'].split('x')))
    tmp = '/tmp/vmctl-%d.ppm' % os.getpid()
    hmp('screendump "%s"' % tmp, 0.1)
    for _ in range(50):
        try:
            with open(tmp, 'rb') as f:
                hdr = f.read(32).split()   # P6 <width> <height> 255
            if len(hdr) >= 3:
                os.remove(tmp)
                return int(hdr[1]), int(hdr[2])
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    sys.exit('vmctl: could not read the screen size from the VM')

SIZE = None
# An installed Tiger scales tablet input about the centre (the installer doesn't)
SCALE = float(os.environ.get('VMCTL_SCALE', '1.1775'))
def absev(x, y):
    global SIZE
    if SIZE is None:
        SIZE = screen_size()
    W, H = SIZE
    x = (x - W / 2) / SCALE + W / 2
    y = (y - H / 2) / SCALE + H / 2
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1)
    return {'execute': 'input-send-event', 'arguments': {'events': [
        {'type': 'abs', 'data': {'axis': 'x', 'value': int(x * 32767 / (W - 1))}},
        {'type': 'abs', 'data': {'axis': 'y', 'value': int(y * 32767 / (H - 1))}}]}}
def btn(down, b='left'):
    return {'execute': 'input-send-event', 'arguments': {'events': [
        {'type': 'btn', 'data': {'down': down, 'button': b}}]}}

def move(x, y):
    qmp([absev(x, y)])
def click(x, y, b='left', n=1):
    qmp([absev(x, y)]); time.sleep(0.15)
    for i in range(n):
        qmp([btn(True, b)]); time.sleep(0.08); qmp([btn(False, b)]); time.sleep(0.08)
def drag(x0, y0, x1, y1, steps=20):
    qmp([absev(x0, y0)]); time.sleep(0.2); qmp([btn(True)]); time.sleep(0.2)
    for i in range(1, steps + 1):
        qmp([absev(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)]); time.sleep(0.05)
    time.sleep(0.2); qmp([btn(False)])

def typ(text, delay=0.03):
    s = socket.create_connection(('127.0.0.1', 4444))
    s.settimeout(0.5)
    try:
        s.recv(4096)
    except socket.timeout:
        pass
    for c in text:
        s.sendall(('sendkey %s\n' % keyname(c)).encode())
        time.sleep(delay)
    time.sleep(0.3)
    s.close()

def read_ppm(path, timeout=10):
    """Width, height and RGB bytes of a P6 screendump, once QEMU has
    written all of it."""
    end = time.time() + timeout
    while True:
        try:
            with open(path, 'rb') as f:
                data = f.read()
            m = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', data)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                pix = data[m.end():]
                if len(pix) >= w * h * 3:
                    return w, h, pix[:w * h * 3]
        except FileNotFoundError:
            pass
        if time.time() > end:
            sys.exit('vmctl: no complete screendump in %s' % path)
        time.sleep(0.05)

def write_png(path, w, h, rgb):
    """RGB bytes as an 8-bit truecolour PNG (no image libraries needed)."""
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    stride = w * 3
    raw = b''.join(b'\0' + rgb[y * stride:(y + 1) * stride] for y in range(h))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' +
                chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))

def shot(path):
    """Screenshot as PNG, or as QEMU's raw PPM if path ends in .ppm."""
    ppm = path.endswith('.ppm')
    tmp = path if ppm else path + '.ppm'
    if os.path.exists(tmp):
        os.remove(tmp)
    hmp('screendump "%s"' % os.path.abspath(tmp), 0.3)
    w, h, rgb = read_ppm(tmp)
    if not ppm:
        write_png(path, w, h, rgb)
        os.remove(tmp)

NARGS = {'cmd': (1, 2), 'type': (1, 1), 'key': (1, 1), 'shot': (1, 1),
         'click': (2, 3), 'dclick': (2, 2), 'move': (2, 2), 'drag': (4, 4)}

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] not in NARGS or not NARGS[a[0]][0] <= len(a) - 1 <= NARGS[a[0]][1]:
        sys.exit(__doc__)
    if a[0] == 'cmd': print(hmp(a[1], float(a[2]) if len(a) > 2 else 0.5))
    elif a[0] == 'type': typ(a[1])
    elif a[0] == 'key': hmp('sendkey ' + a[1])
    elif a[0] == 'shot': shot(a[1])
    elif a[0] == 'click': click(float(a[1]), float(a[2]), a[3] if len(a) > 3 else 'left')
    elif a[0] == 'dclick': click(float(a[1]), float(a[2]), n=2)
    elif a[0] == 'move': move(float(a[1]), float(a[2]))
    elif a[0] == 'drag': drag(*map(float, a[1:5]))
