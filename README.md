# pico-hid host

BLE keyboard injector host for [Raspberry Pi Pico W HID bridge](https://github.com/).  
Connects to the Pico over BLE and injects keystrokes into the target PC via USB HID.

## Quick start

```bash
git clone <repo>
cd pico-hid/host
pip install -r requirements.txt

# Linux
python linux.py

# Windows
python windows.py
```

## Usage

```
python linux.py [DEVICE]            interactive session
python linux.py [DEVICE] -e "cmd"   execute one command and exit
echo "cmd" | python linux.py        pipe mode
```

`DEVICE` is the BLE device name or MAC address (default: `PicoHID`).

### Interactive mode

```
pico> curl -fsSL https://example.com | bash
pico> {WIN+r}
pico> notepad{ENTER}
pico> my password\        ← trailing \ = no Enter sent
pico> !quit
```

### One-liner (like `ssh host "cmd"`)

```bash
python linux.py -e "whoami"
python linux.py 88:A2:9E:02:26:53 -e "reboot"
```

### Pipe mode

```bash
echo "ls -la" | python linux.py
cat commands.txt | python linux.py MyDevice
```

## Special key notation

| Syntax | Key |
|--------|-----|
| `{F1}`–`{F12}` | Function keys |
| `{ESC}` `{TAB}` `{ENTER}` | Common keys |
| `{BS}` `{DEL}` `{INS}` | Backspace / Delete / Insert |
| `{HOME}` `{END}` `{PGUP}` `{PGDN}` | Navigation |
| `{UP}` `{DOWN}` `{LEFT}` `{RIGHT}` | Arrow keys |
| `{CTRL+c}` | Ctrl+C |
| `{WIN+r}` | Win+R |
| `{ALT+F4}` | Alt+F4 |
| `{CTRL+ALT+DEL}` | Ctrl+Alt+Del |
| `{SHIFT+F10}` | Shift+F10 (context menu) |
| `{HANGUL}` `{HANJA}` | Korean IME |

## Pairing notes

**Linux**: The script calls `bluetoothctl pair` automatically.  
If you get a connection error after switching from another PC, remove the old bond first:
```bash
bluetoothctl remove <MAC>
```

**Windows**: Windows handles pairing automatically when first accessing the device.  
If pairing does not prompt, go to *Settings → Bluetooth → Add device* manually.  
To re-pair from a new PC, remove the device from Bluetooth settings first.

## Security

- **BLE link layer**: LESC (LE Secure Connections) with JustWorks pairing — AES-128-CCM
- **Application layer**: ECDH (P-256) handshake every connection → AES-128-CTR session key
- No static PSK — every connection derives a fresh session key (forward secrecy)
