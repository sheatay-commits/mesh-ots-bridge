"""CoT XML builder and parser for the Meshtastic ↔ OTS bridge."""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Marker text format used on the mesh layer (no native marker packet type)
_MRK_RE = re.compile(
    r"^\[MRK\] !(?P<callsign>\S+) (?P<lat>-?\d+\.\d+),(?P<lon>-?\d+\.\d+) (?P<name>.+)$"
)


def _now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _stale_str(minutes):
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _stable_uid(*parts):
    """Deterministic UID from parts — same marker never duplicates on the map."""
    key = "-".join(str(p) for p in parts)
    return "MESH-" + hashlib.md5(key.encode()).hexdigest()[:12].upper()


# ---------------------------------------------------------------------------
# Meshtastic → CoT
# ---------------------------------------------------------------------------

def position_to_cot(packet, callsign):
    """Convert a Meshtastic position packet to a CoT PLI XML string."""
    pos = packet.get("decoded", {}).get("position", {})
    lat = pos.get("latitudeI", 0) / 1e7
    lon = pos.get("longitudeI", 0) / 1e7
    alt = pos.get("altitude", 0)
    node_id = f"!{packet.get('fromId', 'unknown').lstrip('!')}"

    root = ET.Element("event", {
        "version": "2.0",
        "uid": f"MESHTASTIC-{node_id}",
        "type": "a-f-G-U-C",
        "time": _now_str(),
        "start": _now_str(),
        "stale": _stale_str(5),
        "how": "m-g",
    })
    ET.SubElement(root, "point", {
        "lat": str(lat),
        "lon": str(lon),
        "hae": str(alt),
        "ce": "9999999",
        "le": "9999999",
    })
    detail = ET.SubElement(root, "detail")
    label = callsign or node_id
    ET.SubElement(detail, "uid", Droid=label)
    ET.SubElement(detail, "contact", callsign=label)
    return ET.tostring(root, encoding="unicode")


def text_to_cot(packet, callsign, text):
    """Convert a Meshtastic text message to CoT GeoChat XML, or a map marker if [MRK] prefix."""
    m = _MRK_RE.match(text)
    if m:
        return _marker_text_to_cot(m)

    node_id = f"!{packet.get('fromId', 'unknown').lstrip('!')}"
    sender = callsign or node_id
    root = ET.Element("event", {
        "version": "2.0",
        "uid": _stable_uid("chat", node_id, text[:20]),
        "type": "b-t-f",
        "time": _now_str(),
        "start": _now_str(),
        "stale": _stale_str(5),
        "how": "h-g-i-g-o",
    })
    ET.SubElement(root, "point", lat="0", lon="0", hae="0", ce="9999999", le="9999999")
    detail = ET.SubElement(root, "detail")
    remarks = ET.SubElement(detail, "remarks", source=sender, time=_now_str())
    remarks.text = text
    ET.SubElement(detail, "contact", callsign=sender)
    return ET.tostring(root, encoding="unicode")


def _marker_text_to_cot(match):
    """Parse [MRK] text back into a b-m-p-s-m CoT map marker for OTS."""
    lat  = match.group("lat")
    lon  = match.group("lon")
    name = match.group("name")
    callsign = match.group("callsign")

    root = ET.Element("event", {
        "version": "2.0",
        # Stable UID: same callsign+name never creates a duplicate on the map
        "uid": _stable_uid("mrk", callsign, name),
        "type": "b-m-p-s-m",
        "time": _now_str(),
        "start": _now_str(),
        "stale": _stale_str(525600),   # 1 year — persistent until manually deleted
        "how": "h-g-i-g-o",
    })
    ET.SubElement(root, "point", lat=lat, lon=lon, hae="0", ce="9999999", le="9999999")
    detail = ET.SubElement(root, "detail")
    # contact callsign = what ATAK shows as the marker label
    ET.SubElement(detail, "contact", callsign=name)
    ET.SubElement(detail, "usericon", iconsetpath="COT_MAPPING_SPOTMAP/b-m-p-s-m")
    remarks = ET.SubElement(detail, "remarks")
    remarks.text = f"From {callsign} via Meshtastic"
    ET.SubElement(detail, "archive")   # tells ATAK to keep it on the map persistently
    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# CoT → Meshtastic
# ---------------------------------------------------------------------------

def parse_cot(xml_str):
    """
    Parse a CoT XML string. Returns a dict with keys:
      raw_type, uid, lat, lon, alt, text, marker_name, sender_callsign
    Returns None if unparseable.
    """
    try:
        root = ET.fromstring(xml_str.strip())
    except ET.ParseError:
        return None

    cot_type = root.get("type", "")
    uid      = root.get("uid", "")
    point    = root.find("point")
    lat = float(point.get("lat", 0)) if point is not None else 0.0
    lon = float(point.get("lon", 0)) if point is not None else 0.0
    alt = float(point.get("hae", 0)) if point is not None else 0.0

    detail          = root.find("detail")
    text            = None
    marker_name     = None
    sender_callsign = ""

    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            sender_callsign = contact.get("callsign", "")

        remarks = detail.find("remarks")
        if remarks is not None and remarks.text:
            text = remarks.text.strip()

        title = detail.find("title")
        if title is not None and title.text:
            marker_name = title.text.strip()

    # For markers, prefer contact callsign as the display name if no explicit title
    if cot_type.startswith("b-m-p-") and not marker_name:
        marker_name = sender_callsign or uid

    return {
        "raw_type":        cot_type,
        "uid":             uid,
        "lat":             lat,
        "lon":             lon,
        "alt":             alt,
        "text":            text,
        "marker_name":     marker_name,
        "sender_callsign": sender_callsign,
    }


def cot_to_marker_text(cot_dict, gateway_callsign="OTS"):
    """Serialize a b-m-p-* CoT marker to the [MRK] text format for the mesh."""
    name     = cot_dict.get("marker_name") or cot_dict.get("sender_callsign") or "Marker"
    # Use the actual sender callsign if available, fall back to gateway label
    callsign = cot_dict.get("sender_callsign") or gateway_callsign
    lat = cot_dict["lat"]
    lon = cot_dict["lon"]
    return f"[MRK] !{callsign} {lat:.6f},{lon:.6f} {name}"


def atak_forwarder_to_cot(packet, decoded, _buf={}):
    """
    Decode a Meshtastic ATAK_FORWARDER packet into CoT XML.

    Header (12 bytes): b'FT' + msgid(4) + fragid(2) + total_frags(1) +
                       type(1) + 0xdc(1) + offset(1) + data(213 bytes).

    The device sends multiple retransmissions. Two fragments at the same offset
    but with different data XOR together to produce the actual compressed stream
    starting at byte 0 (verified empirically). We store a list of unique data
    values per offset and try all unique pairs.
    """
    import zlib
    node_id = f"!{packet.get('fromId', 'unknown').lstrip('!')}"
    payload = decoded.get("payload", b"")

    if len(payload) < 13 or payload[:2] != b'FT':
        return None, f"ATAK Forwarder (invalid) from {node_id}"

    msg_id      = payload[2:6]
    total_frags = payload[8]
    offset      = payload[11]
    data        = payload[12:]

    logger.info("ATAK Forwarder raw: msgid=%s fragid=%s total=%d offset=0x%02x",
                msg_id.hex(), payload[6:8].hex(), total_frags, offset)

    key = msg_id.hex()
    if key not in _buf:
        _buf[key] = {}   # offset -> list of unique data values
    seen = _buf[key].setdefault(offset, [])
    if data not in seen:
        seen.append(data)

    unique_offsets = len(_buf[key])

    # --- Strategy 1: direct assembly (works if we have offset=0 fragment) ---
    if 0 in _buf[key] and _buf[key][0]:
        buf_size = max(o + len(_buf[key][o][0]) for o in _buf[key])
        buf = bytearray(buf_size)
        for o, vals in _buf[key].items():
            buf[o:o + len(vals[0])] = vals[0]
        try:
            xml = zlib.decompress(bytes(buf)).decode("utf-8")
            logger.info("ATAK Forwarder CoT (direct) from %s: %s", node_id, xml[:120])
            _buf.pop(key, None)
            return xml, f"ATAK Forwarder CoT from {node_id}"
        except Exception:
            pass

    # --- Strategy 2: XOR pairs at same offset to recover compressed stream ---
    for off, vals in _buf[key].items():
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                recovered = bytes(a ^ b for a, b in zip(vals[i], vals[j]))
                if not recovered[:2] == b'\x78\x9c':
                    continue
                # Try full decompress first
                try:
                    xml = zlib.decompress(recovered).decode("utf-8")
                    logger.info("ATAK Forwarder CoT (XOR full) from %s: %s", node_id, xml[:120])
                    _buf.pop(key, None)
                    return xml, f"ATAK Forwarder CoT from {node_id}"
                except Exception:
                    pass
                # Partial decompress — extract what we can
                try:
                    dec = zlib.decompressobj()
                    partial = dec.decompress(recovered).decode("utf-8", errors="replace")
                    xml = _synthesize_cot_from_partial(partial, node_id)
                    if xml:
                        logger.info("ATAK Forwarder CoT (XOR partial) from %s: %s", node_id, partial[:120])
                        _buf.pop(key, None)
                        return xml, f"ATAK Forwarder CoT (partial) from {node_id}"
                except Exception:
                    pass

    return None, f"ATAK Forwarder frag ({unique_offsets} offsets seen) from {node_id}"


def _synthesize_cot_from_partial(partial_xml, node_id):
    """
    Extract uid/type/lat/lon from a truncated CoT XML fragment and build a
    minimal but valid CoT event. Returns None if essential fields are missing.
    """
    import re as _re
    get = lambda attr: (m.group(1) if (m := _re.search(rf'{attr}=["\']([^"\']+)["\']', partial_xml)) else None)
    uid      = get("uid")
    cot_type = get("type")
    lat      = get("lat")
    lon      = get("lon")
    if not (uid and cot_type):
        return None
    # For PLI we need lat/lon; for non-position types (b-*) we can use 0,0
    if lat is None or lon is None:
        if not cot_type.startswith("b-"):
            return None
        lat, lon = "0", "0"
    callsign = get("callsign") or node_id
    root = ET.Element("event", {
        "version": "2.0", "uid": uid, "type": cot_type,
        "time": _now_str(), "start": _now_str(), "stale": _stale_str(5),
        "how": "m-g",
    })
    ET.SubElement(root, "point", lat=lat, lon=lon, hae="0", ce="9999999", le="9999999")
    detail = ET.SubElement(root, "detail")
    ET.SubElement(detail, "contact", callsign=callsign)
    return ET.tostring(root, encoding="unicode")


def atak_plugin_to_cot(packet, decoded):
    """
    Decode a Meshtastic ATAK_PLUGIN packet into CoT XML.
    Parses the TAKPacket protobuf entirely from raw bytes — no atak_pb2 import
    required, since older meshtastic-python versions don't include it.
    Returns (xml_str, summary) or (None, summary) if not forwardable.
    """
    raw_id  = packet.get("fromId", "unknown").lstrip("!")
    node_id = f"!{raw_id}"
    label   = f"MESH-{raw_id.upper()}"   # e.g. MESH-A73AB2FE — no ! in callsign

    payload = decoded.get("payload", b"")
    if not payload:
        return None, f"ATAK (empty) from {node_id}"

    # Detect is_compressed from raw bytes: TAKPacket field 1 (varint) = 1
    # encodes as 0x08 0x01 at the start of the payload.
    is_compressed = len(payload) >= 2 and payload[0] == 0x08 and payload[1] == 0x01

    if is_compressed:
        # Compressed format: lat/lon stored as fixed32 signed ints in field 5
        lat, lon, alt = _parse_compressed_pli(payload)
        if lat is not None:
            root = ET.Element("event", {
                "version": "2.0",
                "uid": _stable_uid("atak-pli", node_id),
                "type": "a-f-G-U-C",
                "time": _now_str(),
                "start": _now_str(),
                "stale": _stale_str(5),
                "how": "m-g",
            })
            ET.SubElement(root, "point", lat=str(lat), lon=str(lon),
                          hae=str(alt or 0), ce="9999999", le="9999999")
            detail = ET.SubElement(root, "detail")
            ET.SubElement(detail, "uid", Droid=label)
            ET.SubElement(detail, "contact", callsign=label)
            return ET.tostring(root, encoding="unicode"), f"ATAK PLI from {label} ({lat:.4f},{lon:.4f})"
        return None, f"ATAK compressed (no position) from {node_id}"

    # Uncompressed: try protobuf decode
    try:
        try:
            from meshtastic.protobuf import atak_pb2
        except ImportError:
            from meshtastic import atak_pb2
        tak = atak_pb2.TAKPacket()
        tak.ParseFromString(payload if isinstance(payload, bytes) else bytes(payload))
        callsign = tak.contact.callsign or tak.contact.device_callsign or node_id
        variant = tak.WhichOneof("payload_variant")

        if variant == "pli":
            lat = tak.pli.latitude_i / 1e7
            lon = tak.pli.longitude_i / 1e7
            alt = tak.pli.altitude
            if lat == 0.0 and lon == 0.0:
                return None, f"ATAK PLI (no GPS) from {callsign}"
            root = ET.Element("event", {
                "version": "2.0",
                "uid": _stable_uid("atak-pli", node_id),
                "type": "a-f-G-U-C",
                "time": _now_str(),
                "start": _now_str(),
                "stale": _stale_str(5),
                "how": "m-g",
            })
            ET.SubElement(root, "point", lat=str(lat), lon=str(lon),
                          hae=str(alt), ce="9999999", le="9999999")
            ET.SubElement(ET.SubElement(root, "detail"), "contact", callsign=callsign)
            return ET.tostring(root, encoding="unicode"), f"ATAK PLI from {callsign}"

        if variant == "chat":
            msg = tak.chat.message
            root = ET.Element("event", {
                "version": "2.0",
                "uid": _stable_uid("atak-chat", node_id, msg[:20]),
                "type": "b-t-f",
                "time": _now_str(),
                "start": _now_str(),
                "stale": _stale_str(5),
                "how": "h-g-i-g-o",
            })
            ET.SubElement(root, "point", lat="0", lon="0", hae="0", ce="9999999", le="9999999")
            d = ET.SubElement(root, "detail")
            ET.SubElement(d, "remarks", source=callsign, time=_now_str()).text = msg
            ET.SubElement(d, "contact", callsign=callsign)
            return ET.tostring(root, encoding="unicode"), f'ATAK chat "{msg[:40]}" from {callsign}'

        return None, f"ATAK (no PLI/chat) from {callsign}"

    except Exception:
        return None, f"ATAK (uncompressed parse error) from {node_id}"


def _parse_compressed_pli(payload):
    """
    Extract lat/lon/alt from a compressed TAKPacket payload.
    When is_compressed=True the firmware stores PLI in TAKPacket field 5
    with lat/lon as fixed32 signed integers (/ 1e7 gives degrees).
    Returns (lat, lon, alt) or (None, None, None).
    """
    import struct

    i = 0
    n = len(payload)

    def read_varint():
        nonlocal i
        val = shift = 0
        while i < n:
            b = payload[i]; i += 1
            val |= (b & 0x7f) << shift
            shift += 7
            if not (b & 0x80):
                return val
        return val

    while i < n:
        tag_byte = payload[i]; i += 1
        field_num = tag_byte >> 3
        wire_type = tag_byte & 0x07

        if wire_type == 0:   # varint — skip
            read_varint()
        elif wire_type == 1: # 64-bit — skip
            i += 8
        elif wire_type == 2: # length-delimited
            length = read_varint()
            sub = payload[i:i + length]; i += length
            if field_num == 5:  # compressed PLI sub-message
                lat = lon = alt = None
                j = 0
                while j < len(sub):
                    stag = sub[j]; j += 1
                    sf = stag >> 3; swt = stag & 7
                    if swt == 5 and j + 4 <= len(sub):  # fixed32
                        raw = struct.unpack('<i', sub[j:j + 4])[0]; j += 4
                        if sf == 1: lat = raw / 1e7
                        elif sf == 2: lon = raw / 1e7
                    elif swt == 0:  # varint
                        val = sh = 0
                        while j < len(sub):
                            b = sub[j]; j += 1
                            val |= (b & 0x7f) << sh; sh += 7
                            if not (b & 0x80): break
                        if sf == 3: alt = val
                    else:
                        break
                return lat, lon, alt
        elif wire_type == 5: # 32-bit — skip
            i += 4

    return None, None, None


def is_allowed(cot_type, allowed_prefixes):
    return any(cot_type.startswith(p) for p in allowed_prefixes)
