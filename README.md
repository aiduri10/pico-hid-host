# pico-hid host

BLE keyboard injector for Raspberry Pi Pico W HID bridge.  
Connects to the Pico over BLE and injects keystrokes into the target PC via USB HID.

## Install

### Linux / macOS

```bash
git clone https://github.com/YOUR/pico-hid
cd pico-hid/host
bash install.sh
```

설치 후:
```bash
pico-hid              # PicoHID 장치 자동 탐색
pico-hid MyDevice     # 이름으로 연결
pico-hid AA:BB:CC:DD:EE:FF  # MAC 주소로 연결
```

### Windows

```powershell
git clone https://github.com/YOUR/pico-hid
cd pico-hid\host
.\install.ps1
```

> PowerShell 스크립트 실행이 막혀있으면 먼저 실행:
> ```powershell
> Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

설치 후 새 터미널에서:
```
pico-hid
```

설치 내용:
- Python 3.10+ 자동 설치 (없을 경우)
- 가상환경 자동 생성 (`~/.local/share/pico-hid/venv`)
- 의존성 자동 설치 (bleak, cryptography)
- `pico-hid` 명령어 PATH 등록

---

## Usage

```
pico-hid [DEVICE]              interactive session
pico-hid [DEVICE] -e "cmd"     execute one command and exit
echo "cmd" | pico-hid          pipe mode
```

### Interactive mode

```
pico> curl -fsSL https://example.com | bash
pico> 안녕하세요               ← Korean auto-converted
pico> {WIN+r}
pico> !raw                     ← SSH-like raw mode
pico> !quit
```

### Raw mode  (`!raw`)

모든 키 입력을 즉시 전달 (Ctrl+C, 화살표, F키 포함)

| 키 | 동작 |
|----|------|
| `~.` | raw mode 종료 |
| `~~` | `~` 문자 전달 |
| `Ctrl+C` | remote에 Ctrl+C 전달 |
| `Ctrl+Z` | remote에 Ctrl+Z 전달 |

### One-liner

```bash
pico-hid -e "whoami"
pico-hid AA:BB:CC:DD:EE:FF -e "reboot"
echo "ls -la" | pico-hid
```

---

## Special key notation

| 표기 | 키 |
|------|----|
| `{F1}`–`{F12}` | 기능키 |
| `{ESC}` `{TAB}` `{ENTER}` | 기본키 |
| `{BS}` `{DEL}` `{INS}` | 백스페이스 / 삭제 / 삽입 |
| `{HOME}` `{END}` `{PGUP}` `{PGDN}` | 이동 |
| `{UP}` `{DOWN}` `{LEFT}` `{RIGHT}` | 화살표 |
| `{CTRL+c}` | Ctrl+C |
| `{WIN+r}` | Win+R |
| `{ALT+F4}` | Alt+F4 |
| `{CTRL+ALT+DEL}` | Ctrl+Alt+Del |
| `{HANGUL}` `{HANJA}` | 한/영 · 한자 |

한글 텍스트는 `{HANGUL}` 없이 직접 입력 가능 (두벌식 자동 변환)

---

## 업데이트

```bash
cd pico-hid/host
git pull
bash install.sh   # 재실행하면 자동 업데이트
```

---

## Pairing

**Linux**: 자동으로 BlueZ pair 시도.  
다른 PC에서 새로 페어링하려면 먼저 기존 본딩 삭제:
```bash
bluetoothctl remove <MAC>
```

**Windows**: 첫 연결 시 Windows가 자동으로 페어링 다이얼로그 표시.  
재페어링이 필요하면 설정 → Bluetooth에서 장치 제거 후 재연결.

---

## Security

- **BLE 링크**: LESC (LE Secure Connections) — AES-128-CCM
- **앱 레이어**: ECDH (P-256) 핸드셰이크 → AES-128-CTR 세션 키
- Static PSK 없음 — 매 연결마다 새로운 세션 키 (forward secrecy)
