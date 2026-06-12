"""Shared protocol: keymap, ECDH session, AES-128-CTR encryption."""

import re
import struct

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH, SECP256R1, EllipticCurvePublicKey, generate_private_key,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

DEFAULT_DEVICE   = "PicoHID"
CHAR_HID         = "12340002-0000-1000-8000-00805f9b34fb"
CHAR_PICO_PUBKEY = "12340004-0000-1000-8000-00805f9b34fb"
CHAR_HOST_PUBKEY = "12340005-0000-1000-8000-00805f9b34fb"

RELEASE = bytes(8)

# ── keymap ────────────────────────────────────────────────────────────────────
_MAP: dict[str, tuple[int, int]] = {}

for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _MAP[_c]         = (0x04 + _i, 0x00)
    _MAP[_c.upper()] = (0x04 + _i, 0x02)

for _i, _c in enumerate("1234567890"):
    _MAP[_c] = (0x1E + _i, 0x00)

for _c, _b in zip("!@#$%^&*()", "1234567890"):
    _MAP[_c] = (_MAP[_b][0], 0x02)

_MAP.update({
    " ":    (0x2C, 0x00), "\n":   (0x28, 0x00), "\r":   (0x28, 0x00),
    "\t":   (0x2B, 0x00), "\x1b": (0x29, 0x00), "\x08": (0x2A, 0x00),
    "\x7f": (0x4C, 0x00),
    "-":  (0x2D, 0x00), "=":  (0x2E, 0x00), "[":  (0x2F, 0x00), "]":  (0x30, 0x00),
    "\\": (0x31, 0x00), ";":  (0x33, 0x00), "'":  (0x34, 0x00), "`":  (0x35, 0x00),
    ",":  (0x36, 0x00), ".":  (0x37, 0x00), "/":  (0x38, 0x00),
    "_":  (0x2D, 0x02), "+":  (0x2E, 0x02), "{":  (0x2F, 0x02), "}":  (0x30, 0x02),
    "|":  (0x31, 0x02), ":":  (0x33, 0x02), '"':  (0x34, 0x02), "~":  (0x35, 0x02),
    "<":  (0x36, 0x02), ">":  (0x37, 0x02), "?":  (0x38, 0x02),
})

_SPECIAL: dict[str, tuple[int, int]] = {
    'TAB':      (0x2B, 0x00), 'ESC':      (0x29, 0x00), 'ESCAPE':   (0x29, 0x00),
    'BS':       (0x2A, 0x00), 'BACKSPACE':(0x2A, 0x00),
    'DEL':      (0x4C, 0x00), 'DELETE':   (0x4C, 0x00),
    'INS':      (0x49, 0x00), 'INSERT':   (0x49, 0x00),
    'ENTER':    (0x28, 0x00), 'RETURN':   (0x28, 0x00), 'SPACE':    (0x2C, 0x00),
    'HOME':     (0x4A, 0x00), 'END':      (0x4D, 0x00),
    'PGUP':     (0x4B, 0x00), 'PAGEUP':   (0x4B, 0x00),
    'PGDN':     (0x4E, 0x00), 'PAGEDOWN': (0x4E, 0x00),
    'UP':       (0x52, 0x00), 'DOWN':     (0x51, 0x00),
    'LEFT':     (0x50, 0x00), 'RIGHT':    (0x4F, 0x00),
    'CAPS':     (0x39, 0x00), 'CAPSLOCK': (0x39, 0x00),
    'PRTSC':    (0x46, 0x00), 'SCROLL':   (0x47, 0x00),
    'PAUSE':    (0x48, 0x00), 'NUMLOCK':  (0x53, 0x00), 'APP':      (0x65, 0x00),
    'HANGUL':   (0x88, 0x00), 'HANJA':    (0x89, 0x00),
    'KP/':      (0x54, 0x00), 'KP*':      (0x55, 0x00),
    'KP-':      (0x56, 0x00), 'KP+':      (0x57, 0x00),
    'KPENTER':  (0x58, 0x00), 'KPDOT':    (0x63, 0x00),
    'KP0':      (0x62, 0x00),
    **{f'KP{i}': (0x59 + i - 1, 0x00) for i in range(1, 10)},
    **{f'F{i}':  (0x3A + i - 1, 0x00) for i in range(1, 13)},
}

_MOD: dict[str, int] = {
    'CTRL': 0x01, 'CTL': 0x01, 'CONTROL': 0x01,
    'SHIFT': 0x02,
    'ALT': 0x04,
    'WIN': 0x08, 'GUI': 0x08, 'SUPER': 0x08,
    'RCTRL': 0x10, 'RSHIFT': 0x20, 'RALT': 0x40, 'ALTGR': 0x40,
    'RWIN': 0x80, 'RGUI': 0x80,
}

_TOKEN_RE = re.compile(r'\{([^}]+)\}')


def make_report(keycode: int, modifier: int) -> bytes:
    r = bytearray(8)
    r[0] = modifier
    r[2] = keycode
    return bytes(r)


def _resolve_special(token: str) -> tuple[int, int]:
    parts = [p.strip().upper() for p in token.split('+')]
    modifier, keycode = 0, 0
    for part in parts:
        if part in _MOD:
            modifier |= _MOD[part]
        elif part in _SPECIAL:
            kc, km = _SPECIAL[part]
            keycode = kc
            modifier |= km
        elif len(part) == 1:
            ch = part.lower()
            if ch in _MAP:
                keycode = _MAP[ch][0]
            else:
                raise ValueError(f"Unknown key: {part!r}")
        else:
            raise ValueError(f"Unknown key: {part!r}")
    return keycode, modifier


def parse_tokens(text: str):
    """Yield (is_special, token) pairs from text with {KEY} notation."""
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > pos:
            yield from ((False, ch) for ch in text[pos:m.start()])
        yield (True, m.group(1))
        pos = m.end()
    if pos < len(text):
        yield from ((False, ch) for ch in text[pos:])


def resolve(is_special: bool, token: str) -> tuple[int, int] | None:
    """Return (keycode, modifier) or None if unresolvable."""
    if is_special:
        try:
            return _resolve_special(token)
        except ValueError as e:
            print(f"  (skip: {e})")
            return None
    if token not in _MAP:
        if token.isprintable():
            print(f"  (skip: {repr(token)})")
        return None
    return _MAP[token]


# ── ECDH + AES-128-CTR session ────────────────────────────────────────────────
class Session:
    def __init__(self):
        self._key: bytes = b''
        self._iv:  bytes = b''
        self._ctr: int   = 0
        self._priv       = None

    def start_handshake(self) -> bytes:
        """Generate ephemeral P-256 keypair; return 64-byte raw public key."""
        self._priv = generate_private_key(SECP256R1(), default_backend())
        return self._priv.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)[1:]  # strip 0x04

    def finish_handshake(self, pico_pub_raw: bytes) -> None:
        """Derive AES session key from Pico's 64-byte raw public key."""
        if len(pico_pub_raw) != 64:
            raise ValueError(f"Bad pubkey length: {len(pico_pub_raw)}")
        pico_pub  = EllipticCurvePublicKey.from_encoded_point(
            SECP256R1(), b'\x04' + pico_pub_raw)
        shared    = self._priv.exchange(ECDH(), pico_pub)
        self._key = shared[:16]
        self._iv  = shared[16:24]
        self._ctr = 0

    def encrypt(self, press: bytes, release: bytes) -> bytes:
        """Encrypt press+release (16 bytes = 1 AES block) → 20-byte wire packet."""
        nonce = self._iv + struct.pack('>I', self._ctr) + b'\x00' * 4
        enc   = Cipher(algorithms.AES(self._key), modes.CTR(nonce)).encryptor()
        ct    = enc.update(press + release) + enc.finalize()
        pkt   = struct.pack('>I', self._ctr) + ct
        self._ctr += 1
        return pkt
