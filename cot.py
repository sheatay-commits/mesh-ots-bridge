"""CoT XML builder and parser for the Meshtastic ↔ OTS bridge."""

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

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
    ET.SubElement(detail, "contact", callsign=callsign or node_id)
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


def is_allowed(cot_type, allowed_prefixes):
    return any(cot_type.startswith(p) for p in allowed_prefixes)
