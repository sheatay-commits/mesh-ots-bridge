# CLAUDE-MEMORY — mesh-ots-bridge

**Last updated:** 2026-06-29 ~01:25 PDT  
**Session focus:** Decoding ATAK_FORWARDER portnum packets so ATAK markers appear on OTS map  
**Current git HEAD:** `526d5f6` (branch: main)

---

## What this project is

A Python daemon running on a Raspberry Pi that bridges a Meshtastic radio (USB serial) directly to OpenTakServer's CoT TCP stream (port 8088). No MQTT, no Wi-Fi gateway — direct serial to OTS.

```
[ATAK on Android] ←Meshtastic radio→ [Pi /dev/ttyUSB0] → daemon.py → OTS :8088 → [ATAK map]
```

### Key files
| File | Role |
|------|------|
| `daemon.py` | systemd service, receives Meshtastic packets, dispatches to cot.py, sends to OTS |
| `cot.py` | CoT XML builder — the file being actively debugged |
| `ots_client.py` | TCP send to OTS (fresh connection + SHUT_WR + drain loop per send) |
| `api.py` | Flask REST API on localhost:5199 |
| `config.json` | Runtime config (serial_mode=usb, OTS=127.0.0.1:8088) |

### Deployment
```bash
# On Pi (ss@192.168.1.163):
cd ~/mesh-ots-bridge && git pull && sudo cp cot.py /opt/mesh-ots-bridge/ && sudo systemctl restart mesh-ots-bridge
# Check logs:
sudo journalctl -u mesh-ots-bridge -f --no-pager
```

### Hardware
- **Pi gateway node:** Heltec V3 on `/dev/ttyUSB0`, Meshtastic node ID `!043a24ec` (no GPS)
- **Remote ATAK node:** Android phone, Meshtastic node ID `!a73ab2fe` (~47.641°N, -122.039°W, Redmond WA)
- ATAK connects to OTS over the local network (not through the Pi)

---

## What's already working

### PLI (position) ✅
ATAK_PLUGIN portnum → TAKPacket protobuf → `a-f-G-U-C` CoT → OTS → shows on map as `MESH-A73AB2FE`.

Critical lessons:
- `decoded["payload"]` is raw bytes — meshtastic-python doesn't decode ATAK_PLUGIN for you
- Detect `is_compressed` from `payload[0:2] == b'\x08\x01'`
- Compressed PLI: lat/lon in TAKPacket field 5, sub-fields 1 and 2 as `fixed32` signed ints (÷1e7 = degrees)
- OTS **ignores PLI if callsign contains `!`** — must use `MESH-A73AB2FE` not `!A73AB2FE`
- OTS **ignores PLI if TCP connection closes too fast** — must use `socket.SHUT_WR` + drain loop (not `time.sleep`)

### Chat ✅ (probably — CoT being sent, not yet confirmed in OTS UI)
TEXT_MESSAGE_APP → `b-t-f` GeoChat CoT → forwarded to OTS

---

## Current problem: ATAK_FORWARDER marker decoding

### What ATAK_FORWARDER is
The [paulmandal/atak-forwarder](https://github.com/paulmandal/atak-forwarder) ATAK plugin sends full CoT XML (any type — PLI, markers, chat) as **fragmented, zlib-compressed** Meshtastic packets using `portnum = ATAK_FORWARDER`.

### Packet format (12-byte FT header)
```
bytes [0:2]   = b'FT'            magic
bytes [2:6]   = msgid            same for all fragments of one CoT event
bytes [6:8]   = fragid           unique per packet (even retransmissions get new fragid)
byte  [8]     = total_frags      always 3 in observed traffic
byte  [9]     = type             01 = compressed CoT
byte  [10]    = 0xdc             fixed field
byte  [11]    = offset           see encoding scheme below
bytes [12:]   = data             213 bytes of encoded content
```

### The encoding scheme (empirically reverse-engineered this session)

Each CoT message is sent as 3 fragment types:
1. **N systematic fragments**: `data = compressed_stream[offset : offset+213]` (raw slice of zlib output)
2. **1 parity fragment**: `data = compressed_stream[offset : offset+213] XOR compressed_stream[0:213]`

The parity fragment shares the **same `offset` byte** as one of the systematics. When the device retransmits, different fragids arrive at the same offset with different data — one systematic, one parity.

**Key property:** XOR of systematic + parity at same offset = `compressed_stream[0:213]` (the zlib header + start of compressed data).

**Verified:** XOR result starts with `78 9c 95 53 c1 6e e2 30...` which is valid zlib and decompresses to real CoT XML.

### What we receive over radio

In every observed burst (total=3 fragments):
- 2-3 fragments arrive at different offsets
- At one offset, 2 *different* data values arrive across retransmit cycles → this is the systematic+parity pair
- At other offsets, only 1 data value arrives → these are pure systematics

### Current reconstruction strategy (in `atak_forwarder_to_cot()`)

```
1. Collect fragments: _buf[msgid][offset] = list of unique data values
2. Find XOR pair: for any offset with ≥2 unique values, XOR them → if result starts 0x789c, that's compressed_start = compressed[0:213]
3. Identify systematics:
   - Offset with 1 value → always systematic (data = compressed[offset:offset+213])
   - Offset with 2 values → systematic is whichever one overlaps consistently with compressed_start
     (i.e., val[0 : 213-offset] == compressed_start[offset:213])
4. Assemble bytearray: write systematics, then overwrite [0:213] with compressed_start
5. zlib.decompress(stream) → CoT XML → forward to OTS
6. Fallback: decompressobj partial decompress → regex-extract uid/type/lat/lon → synthesize minimal CoT
```

### Current failure (as of last test, commit 526d5f6)

**Test case:** msgid=4e70a86f, fragments received:
- offset=0x00 (1 value): `789c9553c16ee23010fd15df7c32c44e...` — this IS compressed[0:213] directly
- offset=0xaf=175 (2 values): systematic + parity pair → XOR = `789c9553c16ee230...` (same start, different CoT timestamp)
- offset=0xff=255 (1 value): `1b2d6d161027912e...` = compressed[255:468]

Assembled 468 bytes, but **zlib.decompress fails: "incomplete or truncated stream"**.

Partial decompression gives:
```
<?xml ... uid='be308fe6-...' type='a-f-G' time='...'><point lat='47.641739' ltaaaaaaaaaa
```

The `ltaaaaaaaaaa` garbling starts right where the buffer join happens.

**Root cause hypothesis:** Gap at bytes [213:255] is zero-filled or corrupted. Coverage is:
- [0:213] from compressed_start ✓
- [175:388] from systematic at offset=0xaf — but the overlap consistency check may be selecting the PARITY fragment instead, putting wrong bytes in [175:388], which pollutes [213:255]
- [255:468] from offset=0xff fragment ✓

The consistency check `val[0:overlap_len] == compressed_start[offset:213]` should identify the systematic, but if both fragments happen to pass (or neither passes), the wrong one gets picked.

### What to try next

**Option A: Log full data values (not just first 32 bytes)**
Change `data[:32].hex()` to `data.hex()` in the log line, then manually XOR the two values at offset=0xaf and verify which one matches the compressed_start overlap.

**Option B: Try both candidates when assembling**
For duplicate-offset pairs, try assembling the stream TWICE (once with each candidate as systematic) and decompress both — the correct one will decompress cleanly:
```python
for candidate_systematic in vals:
    buf[o:o+len(candidate)] = candidate_systematic
    buf[0:213] = compressed_start
    try:
        xml = zlib.decompress(bytes(buf))
        # success!
    except:
        pass  # try the other
```

**Option C: Direct use of offset=0 fragment**
When offset=0 arrives (data starts with `78 9c`), that IS compressed[0:213] directly — no XOR needed. The systematic for other offsets can be identified with the same consistency logic. This worked in the msgid=4e70a86f test case where offset=0 arrived directly.

**Option D: Check if compressed_start is consistent across retransmits**
In msgid=4e70a86f, offset=0xaf XOR gives `789c...` AND offset=0 has direct `789c...` data. They should be identical (same CoT message). If they differ (different timestamp), mixing them will corrupt the stream. The XOR-recovered `compressed_start` must match the current retransmit's offset=0 data if offset=0 is also present.

### Note on CoT type seen

All packets observed so far have `type='a-f-G'` — that's a PLI event, not a map marker. The ATAK_FORWARDER plugin forwards ALL CoT types including PLI. A dropped map pin would show `type='b-m-p-s-m'`. The user hasn't successfully dropped a pin yet (ATAK shows "failed, max retransmission reached" ~30s after broadcast — that's a mesh ACK issue on ATAK's side, not a bridge bug).

---

## File state: cot.py `atak_forwarder_to_cot()` — current logic summary

```python
def atak_forwarder_to_cot(packet, decoded, _buf={}):
    # Parse FT header: msgid=payload[2:6], offset=payload[11], data=payload[12:]
    # _buf[msgid_hex][offset] = list of unique data bytes values
    
    # Step 1: Find compressed_start via XOR of duplicate-offset pair
    # Step 2: Build systematic dict — overlap consistency check for dups
    # Step 3: Assemble buffer, write systematics then compressed_start at [0:213]
    # Step 4: zlib.decompress → return CoT XML
    # Step 5 (fallback): decompressobj partial → regex extract → synthesize CoT
```

Fallback synthesizer `_synthesize_cot_from_partial()`:
- Extracts uid, type, lat, lon via regex from partial XML
- For `b-*` types: uses lat=0, lon=0 if not found (non-positional CoT)
- For `a-*` types: returns None if lat or lon missing (can't place on map without coordinates)

---

## Debug logging currently active (to remove once working)

In `atak_forwarder_to_cot()`:
- `data[:32].hex()` in raw packet log
- XOR hit log with first 8 bytes
- Assembled stream size and start bytes + offsets used
- Partial XML (first 300 chars) on decompress failure

---

## Git history (this session, newest first)

| Commit | What |
|--------|------|
| `526d5f6` | Overlap consistency check to pick systematic vs parity |
| `b06994b` | Reconstruct full stream from systematic + XOR-recovered start |
| `40c6c5e` | Log partial XML content and synthesis failure reason |
| `300bb21` | Log direct assembly failure reason |
| `b0fca85` | Try XOR across all fragment pairs (not just same-offset) |
| `845baf5` | Initial XOR pair recovery + _synthesize_cot_from_partial |
| `b6bd1ec` | Log full fragment hex for analysis |
| `9c94207` | Try decompress after each fragment (not just when all arrive) |

---

## SSH access

```bash
ssh -i /path/to/pi_key ss@192.168.1.163
# Key was at: C:\Users\SS\AppData\Local\Temp\claude\...\scratchpad\pi_key
# Key is EPHEMERAL (in Claude's scratchpad) — may need to re-add on new session
# Pi key fingerprint: user ss, standard Raspberry Pi OS
```

If the scratchpad key is gone, user will need to provide it again or set up `~/.ssh/authorized_keys` from Windows.

---

## OTS TCP send pattern (ots_client.py)

```python
sock.sendall(cot_xml.encode())
sock.shutdown(socket.SHUT_WR)      # signal end of data
try:
    sock.settimeout(1.0)
    while sock.recv(4096): pass    # drain OTS response
except: pass
sock.close()
```

**Do not use `time.sleep()` instead** — OTS will not plot the marker before the connection closes.

---

## Remaining tasks

- [ ] Fix gap/corruption in ATAK_FORWARDER stream reconstruction (bytes 213-255 region)
- [ ] Verify that a properly decoded `b-m-p-s-m` marker CoT appears on OTS map
- [ ] Verify chat (`b-t-f`) appears in OTS
- [ ] Remove all debug logging once ATAK_FORWARDER decoding confirmed working
- [ ] Clean final commit
