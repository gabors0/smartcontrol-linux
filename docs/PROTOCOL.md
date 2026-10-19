# Sennheiser MOMENTUM True Wireless 4 – control protocol

Reverse engineered from *Sennheiser Smart Control Plus* 1.6.3 (`com.sonova.chb.control`,
versionCode 72844) and checked against a real MTW4 running firmware 5.22.1.

Evidence levels used below:

* **verified** – sent to a real MTW4 and the effect/response observed
* **read** – the GET was observed on a real MTW4; the SET format comes from the app code
* **code** – recovered from the app only

## Transport

* Bluetooth Classic, RFCOMM. The SDP record named **GAIA** with service class
  `a2129ff3-081b-4c45-8afe-469d9c4842ec` (MTW4: channels 11 and 12, both work).
* **Never** open the record `00000000-deca-fade-deca-deafdecacaff` (channel 14). It is
  Apple iAP2, and earlier research reports that GAIA frames sent there power the
  headset off.

## Framing (Qualcomm GAIA v3 over RFCOMM)

```
FF | 03 | flags | len | vendor(2) | command(2) | payload | [xor checksum]
```

* flags bit0 = trailing XOR checksum, bit1 = 16-bit length (then `len` is 2 bytes)
* command = `feature << 9 | type << 7 | pdu`; type 0 = command, 1 = notification,
  2 = response, 3 = error (payload[0] = status, e.g. 5 = invalid parameter)
* vendors: `0x001D` Qualcomm (core info), `0x0495` Sennheiser (everything else)
* Notifications for a property reuse its **GET** pdu.
* `0x0495 / feature 0 / pdu 7 [featureId]` registers for notifications. The device
  then immediately pushes the current value of that feature's notifiable properties.

In the app, `Gaia3Client.send` builds the header from three dispatch-table getters on
each command class: vendor (selector `+0x17179`), feature (`+0x14337`) and pdu
(`+0x15854`); the payload comes from `+0x1a54d`. Resolving those selectors for every
class gives the full command table (`re/gaia_commands.txt`).

## Feature IDs (vendor 0x0495)

| id | feature | id | feature |
|----|---------|----|---------|
| 0x00 | core | 0x0B | mmiConfig (touch) |
| 0x02 | device | 0x0C | transparentHearing |
| 0x03 | power | 0x0D | anc |
| 0x04 | genericAudio | 0x0E | tws |
| 0x08 | userEq | 0x0F | fittingTest |
| 0x09 | versions | 0x10 | personalizedSound |
| 0x0A | deviceManagement | 0x14 | localName |
| 0x15 | auracast | 0x16 | findDevice |

## Commands

`F/s` = set pdu, `F/g` = get pdu (hex).

| what | set | get | payload | evidence |
|---|---|---|---|---|
| ANC on/off | 0D/04 | 0D/05 | `[0/1]` | verified |
| Transparency on/off | 0C/04 | 0C/05 | `[0/1]` – wins over ANC; ANC-on clears it | verified |
| Transparency level | 0C/02 | 0C/03 | `[0..100]` | verified |
| ANC modes / anti-wind | 0D/00 | 0D/01 | set `[mode, value]`; get `[(mode,value)×3]`; mode 1 = anti-wind (0 off, 1 max) | verified |
| ANC/transparency combo | 0D/02 | 0D/03 | – (side effect: clears transparency; not used) | verified |
| EQ config | – | 08/00 | `[bands, min, max, …]` in 0.1 dB (MTW4: 5, −6.0, +6.0) | verified |
| EQ band | 08/01 | 08/02 | set `[band, gain i8 (0.1 dB)]`; get `[band]→[gain]`; notification = all bands | verified |
| Bass boost | 08/08 | 08/09 | `[0/1]` | verified |
| Touch action | 0B/00 | 0B/01 | set `[button, pattern, action]`; get `[button, pattern]` | verified |
| Touch locked | 0B/06 | 0B/07 | | code |
| Battery | – | 03/03 | `[left %, right %, case %]` | verified |
| Charger state | – | 03/02 | `[left, right]` 0 none, 1 charging, 2 complete | read |
| Auto power-off | 03/00 | 03/01 | set `[0, u16 seconds]`; get `[0]→[0, u16]` | verified |
| Eco mode | 03/08 | ? | | code |
| Gaming mode | 04/17 | 04/18 | `[0/1]` | verified |
| Smart pause | 04/0C | 04/0D | `[0/1]` | read |
| Auto-answer | 04/0A | 04/0B | `[0/1]` | read |
| Comfort call | 04/14 | 04/15 | `[0/1]` | read |
| Sidetone | 04/05 | 04/06 | `[0..4]` (app slider 0..1 × steps=4) | read |
| Prompts | 04/01 | 04/02 | `[0 off, 1 tones, 2 tones+voice]` | read |
| Audio mode | 04/03 | 04/04 | `[0, mode]` 1 userEq, 2 podcast, 3 personalized, 4 PEQ | read |
| Codec | – | 04/00 | see `protocol.Codec` (0 SBC, 1 AAC, 8 aptX Adaptive, 9 Lossless, FF none) | verified |
| Sample rate | – | 04/1A | u32 Hz | verified |
| On-head detection | 02/00 | 02/01 | `[0/1]` | read |
| aptX 96 kHz | 02/05 | 02/06 | `[0/1]` | read |
| aptX Lossless | 02/07 | 02/08 | `[0/1]` | read |
| Firmware | – | 09/01 | 3 × u16 | verified |
| Model id | – | 09/06 | NUL-terminated string | verified |
| Paired devices | – | 0A/00, 0A/01 | `0A/00 → [?, max]`; `0A/01 [i] → [i, ?, ?, name\0]` | read |
| Find: start ringing | 16/02 | – | `[side]` 0 = right, 1 = left. **Extremely loud.** | verified (right) |
| Find: stop | 16/01 | – | `[side]` | verified |
| Find: state | – | 16/03 | `[side] →` notification `[ringing, side]` | verified |

**Dangerous, deliberately not implemented:** `0x0495 00/40` factory reset, `03/0F` reboot,
`0A/12` delete paired device, `14/00` (no-payload action, probably reset device name).

### Touch values

* button: 0 = right, 1 = left
* pattern: 1 tap, 2 double tap, 3 triple tap, 4 long press
* action: 0 none, 1 play/pause, 2 next, 3 previous, 4 vol+, 5 vol−, 6 transparent hearing,
  7 voice assistant, 8 power off, 9 ANC on/off, 0x0D touch lock, 0x11 gaming mode, … (see
  `protocol.TouchAction`). The MTW4 config allows: tap/double/triple: 0,1,2,3,6,7,9 (+0x0D on
  triple); long press: 0,4,5,6,7,9,0x0D.

## Qualcomm vendor (0x001D) core

`00` API version (3.1), `01` supported features, `03` serial, `04` variant name
("MOMENTUM TW 4"), `05` application version ("5.22.1 / <date>").

## Reproducing the RE

`re/` holds the work files (not needed at runtime):

* `re/blutter/` – Blutter, patched to dump the Dart dispatch table (`blutter_out2/dispatch.txt`)
* `re/scripts/annotate.py` – capstone disassembly annotated with object-pool strings
* `re/scripts/gaia_cmds.py` – resolves vendor/feature/pdu for every command class
* `re/scripts/label_cmds.py`, `re/scripts/payload.py` – log-string labels and payload builders
* `re/dl64/v150/.../configs/mtw4-0.json` – the MTW4 capability config (plaintext in app 1.5.0;
  encrypted in 1.6.x)
