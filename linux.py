#!/usr/bin/env python3
"""
pico-hid — Linux client
BLE keyboard injector bridge for Raspberry Pi Pico W

Usage:
  python linux.py [DEVICE]              interactive session
  python linux.py [DEVICE] -e "cmd"     execute one command and exit
  echo "cmd" | python linux.py [DEVICE] pipe mode

DEVICE: device name or MAC address (default: PicoHID)
"""

import asyncio
import argparse
import os
import select
import sys
import termios
import threading
import tty

import readline  # noqa: F401 — enables arrow-key history in line mode

from bleak import BleakClient, BleakScanner

from core import (
    CHAR_HID, CHAR_HOST_PUBKEY, CHAR_PICO_PUBKEY,
    DEFAULT_DEVICE, RELEASE,
    Session, make_report, resolve, text_to_keystrokes,
)

# ── UI strings ────────────────────────────────────────────────────────────────
_HELP = """\
  !help   show this message
  !keys   special key reference
  !raw    enter raw mode (every keystroke forwarded — like SSH)
  !quit   disconnect

Line mode:
  type text + Enter  → injected as keystrokes + Enter on remote
  trailing \\         → no Enter sent  (e.g. for passwords/prompts)
  Korean text        → auto-converted via 두벌식
  {CTRL+c}  {WIN+r}  {F5}  {ALT+F4}  — special key notation

Raw mode  (!raw):
  every keystroke forwarded immediately (Ctrl+C, arrows, F-keys all work)
  ~.   disconnect from raw mode
  ~~   send a literal ~\
"""

_KEYS = """\
  {F1}–{F12}                    function keys
  {ESC}  {TAB}  {ENTER}         common keys
  {BS}   {DEL}  {INS}           backspace / delete / insert
  {HOME} {END}  {PGUP}  {PGDN}  navigation
  {UP}   {DOWN} {LEFT}  {RIGHT} arrow keys
  {CAPS} {PRTSC} {SCROLL} {PAUSE} {NUMLOCK}
  {HANGUL}  {HANJA}              Korean IME

Modifiers (combine with +):
  CTRL  SHIFT  ALT  WIN / GUI / SUPER
  RCTRL RSHIFT RALT / ALTGR  RWIN / RGUI

Examples:
  {CTRL+ALT+DEL}    {SHIFT+F10}    {WIN+d}    {CTRL+SHIFT+ESC}\
"""

# ── ANSI escape sequence → HID (keycode, modifier) ───────────────────────────
_ESC_SEQ: dict[bytes, tuple[int, int]] = {
    b'[A':   (0x52, 0x00), b'[B':   (0x51, 0x00),  # Up, Down
    b'[C':   (0x4F, 0x00), b'[D':   (0x50, 0x00),  # Right, Left
    b'[H':   (0x4A, 0x00), b'[F':   (0x4D, 0x00),  # Home, End
    b'[2~':  (0x49, 0x00), b'[3~':  (0x4C, 0x00),  # Insert, Delete
    b'[5~':  (0x4B, 0x00), b'[6~':  (0x4E, 0x00),  # Page Up, Page Down
    b'OP':   (0x3A, 0x00), b'OQ':   (0x3B, 0x00),  # F1, F2 (SS3)
    b'OR':   (0x3C, 0x00), b'OS':   (0x3D, 0x00),  # F3, F4 (SS3)
    b'[11~': (0x3A, 0x00), b'[12~': (0x3B, 0x00),  # F1, F2 (xterm)
    b'[13~': (0x3C, 0x00), b'[14~': (0x3D, 0x00),  # F3, F4
    b'[15~': (0x3E, 0x00), b'[17~': (0x3F, 0x00),  # F5, F6
    b'[18~': (0x40, 0x00), b'[19~': (0x41, 0x00),  # F7, F8
    b'[20~': (0x42, 0x00), b'[21~': (0x43, 0x00),  # F9, F10
    b'[23~': (0x44, 0x00), b'[24~': (0x45, 0x00),  # F11, F12
}

# ASCII bytes that map to dedicated HID keys (not Ctrl+letter)
_RAW_CTRL: dict[int, tuple[int, int]] = {
    0x08: (0x2A, 0x00),  # Backspace
    0x09: (0x2B, 0x00),  # Tab
    0x0A: (0x28, 0x00),  # Enter (LF)
    0x0D: (0x28, 0x00),  # Enter (CR)
    0x7F: (0x2A, 0x00),  # Backspace (delete key in raw mode)
}


# ── BLE helpers ───────────────────────────────────────────────────────────────
async def _scan(target: str):
    print(f"Scanning for '{target}'...", end="", flush=True)
    while True:
        if len(target) == 17 and target.count(":") == 5:
            dev = await BleakScanner.find_device_by_address(target, timeout=8.0)
        else:
            dev = await BleakScanner.find_device_by_name(target, timeout=8.0)
        if dev:
            print(f"\r\033[K✓ Found {dev.name} ({dev.address})")
            return dev
        print(".", end="", flush=True)


async def _pair(client: BleakClient) -> None:
    print("Pairing (BlueZ LESC)...", end="", flush=True)
    try:
        await client.pair()
        print(" done")
    except Exception as e:
        print(f" ({e})")


async def _handshake(client: BleakClient, session: Session) -> None:
    print("ECDH handshake...", end="", flush=True)
    pico_pub = bytes(await client.read_gatt_char(CHAR_PICO_PUBKEY))
    host_pub = session.start_handshake()
    await client.write_gatt_char(CHAR_HOST_PUBKEY, host_pub, response=True)
    await asyncio.sleep(0.35)          # Pico DH computation ~100 ms on RP2040
    session.finish_handshake(pico_pub)
    print(" done")


async def _send(client: BleakClient, session: Session,
                keycode: int, modifier: int) -> None:
    payload = session.encrypt(make_report(keycode, modifier), RELEASE)
    while True:
        try:
            await client.write_gatt_char(CHAR_HID, payload, response=False)
            return
        except Exception:
            await asyncio.sleep(0.005)  # L2CAP TX buffer full — yield and retry


async def send_text(client: BleakClient, session: Session, text: str) -> None:
    for keycode, modifier in text_to_keystrokes(text):
        await _send(client, session, keycode, modifier)


# ── raw mode ──────────────────────────────────────────────────────────────────
async def _raw_mode(client: BleakClient, session: Session) -> None:
    """
    Forward every keystroke directly to the remote PC.
    Terminal is put in raw mode; exit with ~. (tilde-dot) at line start.
    """
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    print("\r\nRaw mode — every keystroke forwarded immediately.\r\n"
          "  ~.   disconnect       ~~   send literal ~\r\n"
          "  Ctrl+C / Ctrl+Z / arrows / F-keys all forwarded to remote.\r\n",
          flush=True)
    tty.setraw(fd)

    # Background thread fills an asyncio queue from stdin (avoids blocking the loop)
    queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=512)
    loop  = asyncio.get_event_loop()
    stop  = threading.Event()

    def _reader():
        while not stop.is_set():
            r, _, _ = select.select([fd], [], [], 0.15)
            if not r:
                continue
            try:
                chunk = os.read(fd, 32)
            except OSError:
                break
            for byte in chunk:
                loop.call_soon_threadsafe(queue.put_nowait, byte)
        loop.call_soon_threadsafe(queue.put_nowait, None)  # EOF sentinel

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    async def _get(timeout: float) -> int | None:
        """Return next byte, -1 on timeout, None on EOF."""
        try:
            return await asyncio.wait_for(queue.get(), timeout)
        except asyncio.TimeoutError:
            return -1

    try:
        at_sol  = True   # at start of line (for ~. detection)
        utf_buf = b''    # accumulator for multi-byte UTF-8

        while client.is_connected:
            b = await _get(0.5)
            if b is None:   # EOF
                break
            if b < 0:       # timeout — re-check connection
                continue

            # ── ~. escape (SSH-style, only recognised at line start) ──────────
            if at_sol and b == ord('~'):
                nxt = await _get(0.5)
                if nxt == ord('.'):
                    break                          # disconnect
                if nxt == ord('~'):
                    kc = resolve(False, '~')
                    if kc: await _send(client, session, *kc)
                    at_sol = False
                    continue
                if nxt is not None and nxt >= 0:
                    b = nxt                        # fall through with next byte
                else:
                    continue

            at_sol = b in (0x0D, 0x0A)

            # ── ESC / ANSI escape sequences ───────────────────────────────────
            if b == 0x1B:
                seq = b''
                c = await _get(0.05)
                if c is None or c < 0:
                    await _send(client, session, 0x29, 0x00)  # bare ESC
                    continue
                seq += bytes([c])
                if c == ord('['):          # CSI: ESC [ ... final-byte
                    while True:
                        c = await _get(0.05)
                        if c is None or c < 0: break
                        seq += bytes([c])
                        if 0x40 <= c <= 0x7E: break
                elif c == ord('O'):        # SS3: ESC O X
                    c = await _get(0.05)
                    if c is not None and c >= 0: seq += bytes([c])
                kc = _ESC_SEQ.get(seq)
                if kc:
                    await _send(client, session, *kc)
                else:
                    await _send(client, session, 0x29, 0x00)
                continue

            # ── dedicated HID keys (Tab, Enter, Backspace) ───────────────────
            if b in _RAW_CTRL:
                await _send(client, session, *_RAW_CTRL[b])
                continue

            # ── Ctrl+A–Z (0x01–0x1A) ─────────────────────────────────────────
            if 0x01 <= b <= 0x1A:
                await _send(client, session, 0x04 + b - 1, 0x01)  # key + CTRL
                continue

            # ── UTF-8 multi-byte (Korean, etc.) ──────────────────────────────
            if b >= 0x80:
                utf_buf += bytes([b])
                try:
                    ch = utf_buf.decode('utf-8')
                    utf_buf = b''
                    for kc in text_to_keystrokes(ch):
                        await _send(client, session, *kc)
                except UnicodeDecodeError:
                    pass  # incomplete sequence — wait for more bytes
                continue

            # ── printable ASCII ───────────────────────────────────────────────
            kc = resolve(False, chr(b))
            if kc:
                await _send(client, session, *kc)

    finally:
        stop.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\r\nLeft raw mode.\r\n", flush=True)


# ── session modes ─────────────────────────────────────────────────────────────
async def _run(client: BleakClient, args) -> None:
    session = Session()
    await _pair(client)
    await _handshake(client, session)

    # ── single-command mode (-e / --execute) ──────────────────────────────────
    if args.execute:
        await send_text(client, session, args.execute + "\n")
        return

    # ── pipe mode (stdin is not a tty) ────────────────────────────────────────
    if not sys.stdin.isatty():
        lines = await asyncio.to_thread(sys.stdin.readlines)
        for line in lines:
            await send_text(client, session, line.rstrip("\n") + "\n")
        return

    # ── interactive mode ──────────────────────────────────────────────────────
    print(f"\nConnected to {client.address}")
    print("Type !help for help, !raw for SSH-like raw mode. Ctrl+D or !quit to exit.\n")

    while client.is_connected:
        try:
            line = await asyncio.to_thread(input, "pico> ")
        except EOFError:
            break

        if not line:
            continue

        if line.startswith("!"):
            cmd = line[1:].strip().lower()
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(_HELP)
            elif cmd == "keys":
                print(_KEYS)
            elif cmd == "raw":
                await _raw_mode(client, session)
            else:
                print(f"Unknown command {line!r}. Type !help for help.")
            continue

        # trailing backslash → suppress Enter on remote
        if line.endswith("\\") and not line.endswith("\\\\"):
            await send_text(client, session, line[:-1])
        else:
            await send_text(client, session, line + "\n")


# ── main ──────────────────────────────────────────────────────────────────────
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python linux.py",
        description="pico-hid — BLE keyboard injector (Linux)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python linux.py                           scan for PicoHID, interactive
  python linux.py MyDevice                  connect to device by name
  python linux.py 88:A2:9E:02:26:53        connect by MAC address
  python linux.py -e "curl ... | bash"      execute one command and exit
  echo "whoami" | python linux.py           pipe mode

tip: remove stale pairing before reconnecting from a different PC:
  bluetoothctl remove <MAC>
""")
    p.add_argument("device", nargs="?", default=DEFAULT_DEVICE,
                   metavar="DEVICE",
                   help="device name or MAC address (default: %(default)s)")
    p.add_argument("-e", "--execute", metavar="CMD",
                   help="send CMD and exit (like ssh host \"cmd\")")
    return p


async def _main():
    args = _parser().parse_args()
    dev  = await _scan(args.device)

    disconnected = asyncio.Event()

    def _on_disconnect(_):
        print("\nConnection lost.")
        disconnected.set()

    async with BleakClient(dev, timeout=30.0,
                           disconnected_callback=_on_disconnect) as client:
        run_task = asyncio.create_task(_run(client, args))
        done, _ = await asyncio.wait(
            [run_task, asyncio.create_task(disconnected.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if run_task not in done:
            run_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nDisconnected.")
