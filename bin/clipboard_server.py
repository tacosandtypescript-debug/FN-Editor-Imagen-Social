"""X11 CLIPBOARD owner que ofrece image/png (y texto) para pegar en Telegram."""
import struct
import sys
import time
from Xlib import X, Xatom, display

PNG = open('/home/isaac/editimg_work/jobs/guille_card.png', 'rb').read()

d = display.Display(':0')
root = d.screen().root
win = root.create_window(0, 0, 1, 1, 0, X.InputOutput, d.screen().root_depth)
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
    elif req.target in (A_PNG, A_JPEG):
        respond(req, req.target, 8, PNG)
    elif req.target in (A_UTF8, A_STRING, A_TEXT):
        respond(req, req.target, 8, b'')
