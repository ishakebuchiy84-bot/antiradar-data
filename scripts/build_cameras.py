#!/usr/bin/env python3
"""AntiRadar by Jovliev: сборка базы камер Узбекистана.

Источники: OpenStreetMap (Overpass), открытый набор muminoff/uzbspeedcameradb,
data/manual.json (камеры, добавленные вручную: мобильные посты, треножники и т.д.).
Результат: data/cameras.json и data/changes.json (сколько нового).
"""
import json, math, re, subprocess, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cameras.json"
CHANGES = ROOT / "data" / "changes.json"
MANUAL = ROOT / "data" / "manual.json"

OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
UZB_DB = "https://raw.githubusercontent.com/muminoff/uzbspeedcameradb/main/data/cameras.json"
DEDUP_METERS = 25
OFFICIAL_PDF = "https://api-portal.gov.uz/uploads/11/2025/07/15/5d27a667-c508-c1a7-04ad-cf53a8e1c4e7_media_.pdf"
SAMPLE = ROOT / "data" / "official_sample.txt"
COORD = re.compile(r"(\d{2}[.,]\d{3,})\s*[,;/\s]\s*(\d{2}[.,]\d{3,})")

QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="UZ"][admin_level=2]->.uz;
(
  node["highway"="speed_camera"](area.uz);
  node["enforcement"~"maxspeed|traffic_signals|average_speed|bus_lane|no_overtaking"](area.uz);
  node["man_made"="surveillance"]["surveillance:zone"="traffic"](area.uz);
);
out body;
"""

COMPASS = {"N": 0, "NNE": 22, "NE": 45, "ENE": 67, "E": 90, "ESE": 112, "SE": 135,
           "SSE": 157, "S": 180, "SSW": 202, "SW": 225, "WSW": 247, "W": 270,
           "WNW": 292, "NW": 315, "NNW": 337}


def parse_bearing(v):
    """'90', 'NE', '90;270' -> список углов (камера может смотреть в обе стороны)."""
    if v is None:
        return []
    out = []
    for p in str(v).replace(",", ";").split(";"):
        p = p.strip().upper()
        if p in COMPASS:
            out.append(COMPASS[p])
        else:
            try:
                out.append(int(float(p)) % 360)
            except ValueError:
                pass
    return out


def parse_speed(v):
    if v is None:
        return None
    try:
        return int(str(v).split()[0])
    except ValueError:
        return None


def classify(tags):
    enf = tags.get("enforcement", "")
    if "average_speed" in enf or tags.get("camera:type") == "average_speed":
        return "average"
    if "traffic_signals" in enf:
        return "redlight"
    if "bus_lane" in enf:
        return "buslane"
    if "ALPR" in tags.get("surveillance:type", "").upper():
        return "alpr"
    if tags.get("highway") == "speed_camera" or "maxspeed" in enf:
        return "speed"
    return "traffic"


def fetch(url, data=None, timeout=240):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "AntiRadar-by-Jovliev/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def from_overpass():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    for url in OVERPASS:
        try:
            res = fetch(url, body)
            cams = []
            for el in res.get("elements", []):
                t = el.get("tags", {})
                cams.append({
                    "lat": round(el["lat"], 6), "lon": round(el["lon"], 6),
                    "type": classify(t),
                    "limit": parse_speed(t.get("maxspeed")),
                    "bearing": parse_bearing(t.get("direction") or t.get("camera:direction")),
                    "name": t.get("name") or t.get("description"),
                    "src": "osm", "osm": el["id"],
                })
            print(f"OSM: {len(cams)}")
            return cams
        except Exception as e:  # noqa
            print(f"overpass {url} failed: {e}", file=sys.stderr)
            time.sleep(5)
    return []


def from_uzbdb():
    try:
        raw = fetch(UZB_DB)
        rows = raw if isinstance(raw, list) else raw.get("cameras", [])
        tmap = {"speed_camera": "speed", "alpr": "alpr", "traffic_camera": "traffic"}
        cams = []
        for r in rows:
            cams.append({
                "lat": round(float(r["latitude"]), 6), "lon": round(float(r["longitude"]), 6),
                "type": tmap.get(r.get("camera_type"), "traffic"),
                "limit": r.get("speed_limit"), "bearing": parse_bearing(r.get("compass_direction")),
                "name": r.get("road_direction"), "src": "uzbdb", "osm": r.get("osm_node_id"),
            })
        print(f"uzbspeedcameradb: {len(cams)}")
        return cams
    except Exception as e:  # noqa
        print(f"uzbdb failed: {e}", file=sys.stderr)
        return []


def classify_text(low):
    if re.search(r"средн\w* скорост", low):
        return "average"
    if re.search(r"светофор|красн", low):
        return "redlight"
    if re.search(r"ремн|телефон", low):
        return "seatbelt"
    if re.search(r"полос|автобус", low):
        return "buslane"
    if re.search(r"радар|скорост|tezlik|tezkor", low):
        return "speed"
    return "traffic"


def from_official():
    """Официальный список СБДД МВД (1831 точка). Берём только строки с координатами."""
    try:
        req = urllib.request.Request(OFFICIAL_PDF, headers={"User-Agent": "AntiRadar-by-Jovliev/1.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        pdf = ROOT / "data" / "official.pdf"
        pdf.write_bytes(data)
        txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
        pdf.unlink(missing_ok=True)
    except Exception as e:  # noqa
        print(f"official PDF failed: {e}", file=sys.stderr)
        return []
    lines = [l for l in txt.splitlines() if l.strip()]
    SAMPLE.write_text("\n".join(lines[:80]), "utf-8")
    cams = []
    for line in lines:
        m = COORD.search(line)
        if not m:
            continue
        a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        if 37 <= a <= 46 and 55 <= b <= 74:
            lat, lon = a, b
        elif 37 <= b <= 46 and 55 <= a <= 74:
            lat, lon = b, a
        else:
            continue
        low = line.lower()
        lim = re.search(r"(\d{2,3})\s*(?:км|km)", low)
        cams.append({"lat": round(lat, 6), "lon": round(lon, 6), "type": classify_text(low),
                     "limit": int(lim.group(1)) if lim else None, "bearing": [],
                     "name": None, "src": "official"})
    print(f"official PDF: {len(cams)} строк с координатами из {len(lines)}")
    return cams


def from_manual():
    if not MANUAL.exists():
        return []
    cams = json.loads(MANUAL.read_text("utf-8"))
    for c in cams:
        c.setdefault("src", "manual")
        c.setdefault("bearing", [])
        c.setdefault("limit", None)
        c.setdefault("name", None)
    return cams


def dist_m(a, b):
    R = 6371000
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def dedupe(cams):
    """Приоритет: manual > osm > uzbdb. Дубликат = тот же тип в радиусе 25 м.
    Дубль не выбрасываем вслепую: дополняем оригинал лимитом, направлением, названием."""
    order = {"manual": 0, "official": 1, "osm": 2, "uzbdb": 3}
    cams = sorted(cams, key=lambda c: order.get(c["src"], 9))
    cell = {}
    kept = []
    for c in cams:
        key = (int(c["lat"] * 200), int(c["lon"] * 200))
        dup = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in cell.get((key[0] + dx, key[1] + dy), []):
                    dm = dist_m(k, c)
                    if k["type"] == c["type"] and dm <= DEDUP_METERS:
                        dup = k
        if dup:
            dup["limit"] = dup["limit"] or c.get("limit")
            dup["bearing"] = dup["bearing"] or c.get("bearing") or []
            dup["name"] = dup["name"] or c.get("name")
            continue
        cell.setdefault(key, []).append(c)
        kept.append(c)
    return kept


def stable_id(c):
    # Keep existing IDs except where first-letter prefixes collide.
    prefix = {"seatbelt": "sb", "average": "av"}.get(c["type"], c["type"][0])
    return f"{prefix}{int(round(c['lat'] * 1e5))}_{int(round(c['lon'] * 1e5))}"


def main():
    cams = from_manual() + from_official() + from_overpass() + from_uzbdb()
    if not cams:
        sys.exit("Нет данных ни из одного источника, базу не трогаю.")
    cams = dedupe(cams)
    for c in cams:
        c["id"] = stable_id(c)
    cams.sort(key=lambda c: c["id"])

    old_ids, version = set(), 0
    old_cameras = []
    if OUT.exists():
        old = json.loads(OUT.read_text("utf-8"))
        old_cameras = old.get("cameras", [])
        old_ids = {c["id"] for c in old.get("cameras", [])}
        version = old.get("version", 0)

    new_ids = {c["id"] for c in cams}
    added, removed = len(new_ids - old_ids), len(old_ids - new_ids)
    if old_ids and old_cameras == cams:
        print("Изменений нет.")
        return
    if old_ids and len(new_ids) < len(old_ids) * 0.7:
        sys.exit(f"Подозрительно мало камер ({len(new_ids)} против {len(old_ids)}), отмена.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.write_text(json.dumps({"version": version + 1, "generated": now, "count": len(cams),
                               "cameras": cams}, ensure_ascii=False, separators=(",", ":")), "utf-8")
    CHANGES.write_text(json.dumps({"version": version + 1, "generated": now, "added": added,
                                   "removed": removed, "total": len(cams)}, ensure_ascii=False), "utf-8")
    print(f"v{version + 1}: всего {len(cams)}, новых {added}, убрано {removed}")


if __name__ == "__main__":
    main()
