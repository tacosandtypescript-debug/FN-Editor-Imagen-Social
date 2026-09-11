"""X11 CLIPBOARD owner que ofrece image/png (y texto) para pegar en Telegram."""
import argparse
from io import BytesIO
import os
import struct
from pathlib import Path
from PIL import Image
from Xlib import X, Xatom, display
from Xlib.error import DisplayNameError

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument(
    'image', nargs='?', default=str(ROOT / 'examples' / 'historical' / 'guille_card.png'),
    help='PNG que se ofrecerá en el portapapeles',
)
a = ap.parse_args()
with open(a.image, 'rb') as fh:
    PNG = fh.read()
with Image.open(a.image) as image:
    jpeg_buffer = BytesIO()
    image.convert('RGB').save(jpeg_buffer, format='JPEG', quality=95, optimize=True)
    JPEG = jpeg_buffer.getvalue()

try:
    d = display.Display(os.environ.get('DISPLAY', ':0'))
except DisplayNameError as exc:
    raise SystemExit('no se pudo conectar al display X11; configura DISPLAY') from exc
root = d.screen().root
win = root.create_window(0, 0, 1, 1, 0, X.InputOutput, X.CopyFromParent)
win.set_selection_owner(d.intern_atom('CLIPBOARD'), X.CurrentTime)

A_TARGETS = d.intern_atom('TARGETS')
A_PNG = d.intern_atom('image/png')
A_JPEG = d.intern_atom('image/jpeg')
A_UTF8 = d.intern_atom('UTF8_STRING')
A_STRING = d.intern_atom('STRING')
A_TEXT = d.intern_atom('TEXT')

print('clipboard owner ready, png bytes', len(PNG), flush=True)


def respond(req, type_atom, fmt, data):
    req.window.change_property(req.property, type_atom, fmt, data)
    ev = X.SelectionNotify(
        time=X.CurrentTime, requestor=req.window, selection=req.selection,
        target=req.target, property=req.property)
    req.window.send_event(ev)
    d.flush()


while True:
    try:
        e = d.next_event()
    except Exception as ex:
        continue
    if e.type != X.SelectionRequest:
        continue
    req = e
    if req.target == A_TARGETS:
        atoms = [A_TARGETS, A_PNG, A_JPEG, A_STRING, A_UTF8, A_TEXT]
        data = struct.pack('=' + 'L' * len(atoms), *[int(a) for a in atoms])
        respond(req, Xatom.ATOM, 32, data)
    elif req.target == A_PNG:
        respond(req, req.target, 8, PNG)
    elif req.target == A_JPEG:
        respond(req, req.target, 8, JPEG)
    elif req.target in (A_UTF8, A_STRING, A_TEXT):
        respond(req, req.target, 8, b'')
