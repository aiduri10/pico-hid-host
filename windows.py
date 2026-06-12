#!/usr/bin/env python3
"""
pico-hid — Windows client
BLE keyboard injector bridge for Raspberry Pi Pico W

Usage:
  python windows.py [DEVICE]              interactive session
  python windows.py [DEVICE] -e "cmd"     execute one command and exit
  echo cmd | python windows.py [DEVICE]   pipe mode

DEVICE: device name or MAC address (default: PicoHID)

Note: Windows handles BLE pairing automatically via the WinRT BLE stack.
A system pairing dialog may appear on the first connection.
If it does not appear, manually pair via Settings > Bluetooth.
"""

import asyncio
import argparse
import sys
import threading

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

# ── Windows virtual scan code → HID (keycode, modifier) ─────────────────────
# Received after 0x00 or 0xE0 prefix from msvcrt.getwch()
_WIN_SPECIAL: dict[int, tuple[int, int]] = {
    0x48: (0x52, 0x00), 0x50: (0x51, 0x00),  # Up, Down       (0xE0 prefix)
    0x4B: (0x50, 0x00), 0x4D: (0x4F, 0x00),  # Left, Right
    0x47: (0x4A, 0x00), 0x4F: (0x4D, 0x00),  # Home, End
    0x49: (0x4B, 0x00), 0x51: (0x4E, 0x00),  # Page Up, Page Down
    0x52: (0x49, 0x00), 0x53: (0x4C, 0x00),  # Insert, Delete
    0x3B: (0x3A, 0x00), 0x3C: (0x3B, 0x00),  # F1,  F2        (0x00 prefix)
    0x3D: (0x3C, 0x00), 0x3E: (0x3D, 0x00),  # F3,  F4
    0x3F: (0x3E, 0x00), 0x40: (0x3F, 0x00),  # F5,  F6
    0x41: (0x40, 0x00), 0x42: (0x41, 0x00),  # F7,  F8
    0x43: (0x42, 0x00), 0x44: (0x43, 0x00),  # F9,  F10
    0x85: (0x44, 0x00), 0x86: (0x45, 0x00),  # F11, F12
}

_RAW_CTRL: dict[int, tuple[int, int]] = {
    0x08: (0x2A, 0x00),  # Backspace
    0x09: (0x2B, 0x00),  # Tab
    0x0D: (0x28, 0x00),  # Enter
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
            print(f"\rFound {dev.name} ({dev.address})                    ")
            return dev
        print(".", end="", flush=True)


async def _pair(client: BleakClient) -> None:
    # WinRT pairs automatically when accessing an encrypted GATT characteristic.
    print("Pairing: Windows handles automatically on first encrypted access.")


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
            await asyncio.sleep(0.005)


async def send_text(client: BleakClient, session: Session, text: str) -> None:
    for keycode, modifier in text_to_keystrokes(text):
        await _send(client, session, keycode, modifier)


# ── raw mode ──────────────────────────────────────────────────────────────────
async def _raw_mode(client: BleakClient, session: Session) -> None:
    """
    Forward every keystroke directly to the remote PC using msvcrt.
    Exit with ~. (tilde-dot) at line start.
    """
    import msvcrt
    import time

    print("\nRaw mode — every keystroke forwarded immediately.\n"
          "  ~.   disconnect       ~~   send literal ~\n"
          "  Ctrl+C / Ctrl+Z / arrows / F-keys all forwarded to remote.\n",
          flush=True)

    # Background thread reads keystrokes and delivers them to asyncio queue.
    # msvcrt.getwch() blocks until a key is pressed, so we poll with kbhit().
    queue: asyncio.Queue = asyncio.Queue(maxsize=512)
    loop  = asyncio.get_event_loop()
    stop  = threading.Event()

    def _reader():
        while not stop.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.005)
                continue
            ch = msvcrt.getwch()
            code = ord(ch)
            if code in (0x00, 0xE0):
                # Two-byte special key: prefix + scan code
                ch2   = msvcrt.getwch()
                code2 = ord(ch2)
                loop.call_soon_threadsafe(queue.put_nowait, code)
                loop.call_soon_threadsafe(queue.put_nowait, code2)
            elif code > 0xFF:
                # Wide Unicode character (Korean from IME, etc.)
                loop.call_soon_threadsafe(queue.put_nowait, ch)  # deliver as str
            else:
                loop.call_soon_threadsafe(queue.put_nowait, code)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    async def _get(timeout: float) -> int | str | None:
        try:
            return await asyncio.wait_for(queue.get(), timeout)
        except asyncio.TimeoutError:
            return -1  # timeout sentinel

    try:
        at_sol = True

        while client.is_connected:
            b = await _get(0.5)
            if b is None:   # EOF
                break
            if b == -1:     # timeout
                continue

            # ── Unicode character (Korean from IME) ──────────────────────────
            if isinstance(b, str):
                for kc in text_to_keystrokes(b):
                    await _send(client, session, *kc)
                at_sol = False
                continue

            # ── Windows special key (0x00 / 0xE0 prefix) ────────────────────
            if b in (0x00, 0xE0):
                scan = await _get(0.2)
                if isinstance(scan, int) and scan >= 0:
                    kc = _WIN_SPECIAL.get(scan)
                    if kc: await _send(client, session, *kc)
                continue

            # ── ~. escape ────────────────────────────────────────────────────
            if at_sol and b == ord('~'):
                nxt = await _get(0.5)
                if nxt == ord('.'):
                    break
                if nxt == ord('~'):
                    kc = resolve(False, '~')
                    if kc: await _send(client, session, *kc)
                    at_sol = False
                    continue
                if nxt is not None and nxt != -1:
                    b = nxt
                else:
                    continue

            at_sol = b in (0x0D, 0x0A)

            # ── ESC ──────────────────────────────────────────────────────────
            if b == 0x1B:
                await _send(client, session, 0x29, 0x00)
                continue

            # ── dedicated HID keys ───────────────────────────────────────────
            if b in _RAW_CTRL:
                await _send(client, session, *_RAW_CTRL[b])
                continue

            # ── Ctrl+A–Z ─────────────────────────────────────────────────────
            if 0x01 <= b <= 0x1A:
                await _send(client, session, 0x04 + b - 1, 0x01)
                continue

            # ── printable ASCII ───────────────────────────────────────────────
            kc = resolve(False, chr(b))
            if kc:
                await _send(client, session, *kc)

    finally:
        stop.set()
        print("\nLeft raw mode.\n", flush=True)


# ── session modes ─────────────────────────────────────────────────────────────
async def _run(client: BleakClient, args) -> None:
    session = Session()
    await _pair(client)
    await _handshake(client, session)

    if args.execute:
        await send_text(client, session, args.execute + "\n")
        return

    if not sys.stdin.isatty():
        lines = await asyncio.to_thread(sys.stdin.readlines)
        for line in lines:
            await send_text(client, session, line.rstrip("\n") + "\n")
        return

    print(f"\nConnected to {client.address}")
    print("Type !help for help, !raw for SSH-like raw mode. Ctrl+Z+Enter or !quit to exit.\n")

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

        if line.endswith("\\") and not line.endswith("\\\\"):
            await send_text(client, session, line[:-1])
        else:
            await send_text(client, session, line + "\n")


# ── main ──────────────────────────────────────────────────────────────────────
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python windows.py",
        description="pico-hid — BLE keyboard injector (Windows)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python windows.py                         scan for PicoHID, interactive
  python windows.py MyDevice                connect to device by name
  python windows.py 88:A2:9E:02:26:53      connect by MAC address
  python windows.py -e "curl ... | bash"    execute one command and exit
  echo whoami | python windows.py           pipe mode

tip: if pairing fails, remove the device in Settings > Bluetooth first.
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
