#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_cbs.py — עדכון אוטומטי של data/IL.json ממדד המחירים לצרכן (הלמ"ס)
=======================================================================
רץ ע"י GitHub Action מדי חודש. מושך את מדד המחירים לצרכן הכללי (קוד 120010)
מ-API של הלשכה המרכזית לסטטיסטיקה, וכותב data/IL.json בפורמט שהדשבורד צורך:
  { source, agency, asof, monthly:[[dd.mm.yyyy, ערך-2024=100], ...],
    exact:{date:{base:ערך}}, published:{date:{m, y12}} }

per-base מחושב מהערך הרשמי (2024=100) × מקדמי הקישור המדויקים של הלמ"ס,
ולכן זהה לפרסום. אחוזי השינוי נלקחים מהלמ"ס אם קיימים בתשובה, אחרת מחושבים.

ריצה מקומית לבדיקה:  python scripts/update_cbs.py
"""
import os, json, sys, datetime
from urllib.request import Request, urlopen

CODE  = os.environ.get("CBS_CODE", "120010")
START = os.environ.get("CBS_START", "01-2015")
# נקודת הקצה הרשמית של הלמ"ס. ניתן לעקוף עם CBS_URL.
URL = os.environ.get("CBS_URL",
    "https://api.cbs.gov.il/index/data/price?id={code}&format=json&download=false&startPeriod={start}"
).format(code=CODE, start=START)

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "IL.json")

# מקדמי קישור מדויקים (לוח ג.1.1 של הלמ"ס, ירחון יולי 2026): ערך(בסיס)=ערך(2024=100)/v*100
BASE_V = {
    "1993=100": 39.32828827, "2008=100": 77.08144804, "2010=100": 81.78341637,
    "2012=100": 86.03615402, "2018=100": 87.65946697, "2020=100": 87.92244537,
    "2022=100": 93.10986965, "2024=100": 100.0,
}
EXPORT_BASES = list(BASE_V.keys())


def num(x):
    try:    return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError): return None


def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (cpi-manager)",
                                "Accept": "application/json"})
    with urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def walk(obj, out):
    """פירסור גמיש: מאתר רשומות חודשיות (תאריך + ערך מדד + אחוזים) בכל מבנה JSON."""
    if isinstance(obj, dict):
        date = obj.get("date") or obj.get("Date") or obj.get("month") or obj.get("period")
        val = None
        for k in ("value", "Value", "currBaseValue", "index"):
            if k in obj and num(obj[k]) is not None:
                val = num(obj[k]); break
        if val is None and isinstance(obj.get("currBase") or obj.get("CurrBase"), dict):
            val = num((obj.get("currBase") or obj.get("CurrBase")).get("value"))
        if date and val is not None:
            m = next((num(obj[k]) for k in ("percentMonth","PercentMonth","monthlyChange","percent_month") if num(obj.get(k)) is not None), None)
            y = next((num(obj[k]) for k in ("percentYear","PercentYear","yearlyChange","percent_year","percent12") if num(obj.get(k)) is not None), None)
            out.append({"date": str(date), "value": val, "m": m, "y12": y})
        for v in obj.values():
            walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out)


def norm_date(s):
    s = str(s).strip().split("T")[0].replace("/", "-")
    p = s.split("-")
    if len(p) < 2: return None
    if len(p[0]) == 4: y, m = p[0], p[1]
    else:             m, y = p[0], p[1]
    return f"01.{int(m):02d}.{int(y)}"


def key_desc(d):
    dd, mm, yy = d.split("."); return (int(yy), int(mm))


def build():
    raw = fetch(URL)
    recs = []
    walk(raw, recs)
    by_date = {}
    for r in recs:
        d = norm_date(r["date"])
        if d and d not in by_date:
            by_date[d] = r
    if not by_date:
        raise SystemExit("ERROR: no CPI records parsed from CBS response — check schema/URL")

    dates = sorted(by_date, key=key_desc, reverse=True)   # מהחדש לישן
    monthly = [[d, round(by_date[d]["value"], 4)] for d in dates]
    lut = {d: by_date[d]["value"] for d in dates}

    exact, published = {}, {}
    for i, d in enumerate(dates):
        v = round(by_date[d]["value"], 4)
        exact[d] = {b: round(v / BASE_V[b] * 100, 4) for b in EXPORT_BASES}
        # אחוז חודשי: מהלמ"ס אם קיים, אחרת חישוב מול החודש הקודם (עיגול לספרה)
        prev = dates[i + 1] if i + 1 < len(dates) else None
        mchg = by_date[d]["m"]
        if mchg is None and prev and lut[prev]:
            mchg = round((v / lut[prev] - 1) * 100, 1)
        # אחוז 12 חודשים: מהלמ"ס אם קיים, אחרת מול אותו חודש אשתקד
        dd, mm, yy = d.split(".")
        py = f"{dd}.{mm}.{int(yy) - 1}"
        ychg = by_date[d]["y12"]
        if ychg is None and py in lut and lut[py]:
            ychg = round((v / lut[py] - 1) * 100, 1)
        pub = {}
        if mchg is not None: pub["m"] = round(mchg, 1)
        if ychg is not None: pub["y12"] = round(ychg, 1)
        if pub: published[d] = pub

    return {
        "source": "live (api.cbs.gov.il)",
        "agency": "CBS",
        "asof": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "monthly": monthly,
        "exact": exact,
        "published": published,
    }


def main():
    data = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"wrote {OUT}: {len(data['monthly'])} months, newest {data['monthly'][0]}")


if __name__ == "__main__":
    main()
