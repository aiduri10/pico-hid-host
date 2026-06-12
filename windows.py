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

from bleak import BleakClient, BleakScanner

from core import (
    CHAR_HID, CHAR_HOST_PUBKEY, CHAR_PICO_PUBKEY,
    DEFAULT_DEVICE, RELEASE,
    Session, make_report, parse_tokens, resolve,
)

# ── UI strings ────────────────────────────────────────────────────────────────
_HELP = """\
  !help   show this message
  !keys   special key reference
  !quit   disconnect

Text: type and press Enter → injected as keystrokes + Enter on remote PC
  line ending with \\   → sent without Enter  (e.g. for passwords)
  {CTRL+c}             → Ctrl+C
  {WIN+r}              → Win+R
  {F5}                 → F5
  {ALT+F4}             → close window\
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
    # A Windows system dialog appears on first connection — accept it.
    # If nothing happens, pair manually: Settings > Bluetooth > Add device.
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
            await asyncio.sleep(0.005)  # L2CAP TX buffer full — yield and retry


async def send_text(client: BleakClient, session: Session, text: str) -> None:
    for is_special, token in parse_tokens(text):
        kc = resolve(is_special, token)
        if kc:
            await _send(client, session, kc[0], kc[1])


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
    print("Type !help for help. Ctrl+Z+Enter or !quit to exit.\n")

    while client.is_connected:
        try:
            line = await asyncio.to_thread(input, "pico> ")
        except EOFError:
            break

        if not line:
            continue

        # meta commands
        if line.startswith("!"):
            cmd = line[1:].strip().lower()
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(_HELP)
            elif cmd == "keys":
                print(_KEYS)
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
