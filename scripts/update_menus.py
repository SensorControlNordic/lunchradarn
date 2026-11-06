#!/usr/bin/env python3
"""Hämtar dagens lunchmenyer till SCN Lunchradarn och skriver menus.json.

Körs av GitHub Actions varje vardagsmorgon (.github/workflows/menyer.yml).
Läser bara restaurangernas egna, öppna sidor. Kvartersmenyn och andra sidor
som stoppar automatiska läsare används aldrig.

Test utan nätverk:  python3 scripts/update_menus.py --offline <mapp med .txt> --date 2026-11-06
"""
import argparse, datetime as dt, json, os, re, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
UA = "Mozilla/5.0 (compatible; SCN-Lunchradarn/1.0; intern lunchsida)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("LR_OUT") or os.path.join(ROOT, "menus.json")

SOURCES = {
    "bredbar": "https://bredbarsolna.se/dagens-lunch-frosunda/",
    "matronan": "https://www.matronan.se/",
    "bramat": "https://lunchmap.se/restaurant/27c23c87-a344-4635-95a1-c63e2cb34f2e",
    "greven": "https://xn--grevenochbetjnten-2qb.se/lunchmeny/",
    "kitchenclub": "https://app.kanpla.dk/app/s/JoQf98Sr0s2HtmzuVQre/m/5gkWaa5RkwOX7nSLmwMD",
}
DAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag"]
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
          "september", "oktober", "november", "december"]

# ---------- klassning ----------
FISH = r"fisk|lax|torsk|kolja|sej|kummel|flundra|rödspätta|strömming|räk|kräft|skaldjur|tonfisk|bouillabaisse|fish|mussl|sill|gös|abborre|kungsfisk|havets|hälleflundra|röding|tilapia|pangasius|scampi|bläckfisk|krabba|hummer"
MEAT = r"kyckling|fläsk|bacon|biff|kalv|oxe|\bnöt|pork|chicken|schnitzel|köttbull|korv|lamm|skinka|wallenbergare|färs|burgare|entrecote|tritip|beef|karré|pulled|kött|anka|kalkon|yakiniku|pljeskavica|bourguignon|carbonara|kebab|chorizo|salsiccia|högrev|fläskfilé|grillad"
VEG = r"vegetari|vegan|växtbaser|halloumi|falafel|chèvre|chevre|quorn|tofu|paneer|svamp|bönor|linser|kikärt|ramen|fritters|grönsak|aubergin|pesto|fetaost"


def classify(text, hint=None):
    if hint:
        return hint
    t = text.lower()
    if re.search(FISH, t):
        return "fisk"
    meat = re.search(MEAT, t.replace("havets wallenbergare", ""))
    if re.search(VEG, t) and not meat:
        return "veg"
    if "soppa" in t and not meat:
        return "soppa"
    return "kott"


def tidy(s):
    s = s.replace("​", "").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip(" ,;-–")
    s = re.sub(r"\bm\. ", "med ", s)
    s = s.replace(" & ", " och ")
    return s[:1].upper() + s[1:] if s else s


def lines_of(text):
    return [l.replace("​", "").replace("\xa0", " ").strip() for l in text.splitlines()]


def week_dates(today):
    monday = today - dt.timedelta(days=today.weekday())
    return [monday + dt.timedelta(days=i) for i in range(5)]


# ---------- tolkare (tar text, returnerar {"days": {datum: {...}}}) ----------
def parse_weekly_by_dayname(text, today, week_re, extra_re=None, stop_re=None, strip_veg=True):
    L = lines_of(text)
    wk = None
    for l in L:
        m = re.search(week_re, l, re.I)
        if m:
            wk = int(m.group(1)); break
    if wk != today.isocalendar()[1]:
        raise ValueError(f"fel vecka på sidan (v. {wk})")
    dates = dict(zip(DAYS, week_dates(today)))
    days, cur = {}, None
    for l in L:
        if not l:
            continue
        if stop_re and re.search(stop_re, l, re.I):
            cur = None
            if days: break
            continue
        low = l.lower().rstrip(":")
        if low in DAYS:
            cur = dates[low].isoformat(); days[cur] = {"dishes": []}; continue
        if cur is None:
            continue
        if extra_re and re.search(extra_re, l, re.I):
            days[cur]["extra"] = tidy(l); continue
        hint = "veg" if re.match(r"vegetarisk", l, re.I) else None
        n = re.sub(r"^vegetarisk[a-z]*\s+", "", l, flags=re.I) if (hint and strip_veg) else l
        days[cur]["dishes"].append({"t": classify(n, hint), "n": tidy(n)})
    days = {k: v for k, v in days.items() if v["dishes"]}
    if not days:
        raise ValueError("hittade inga rätter")
    return {"days": days}


def parse_matronan(text, today):
    return parse_weekly_by_dayname(text, today, r"^vecka\s+(\d+)", stop_re=r"^hela veckan")


def parse_bramat(text, today):
    return parse_weekly_by_dayname(text, today, r"lunchmeny v\.?\s*(\d+)",
                                   extra_re=r"^(vi bjuder|fredagsgottis)", stop_re=r"^alltid på menyn")


def parse_greven(text, today):
    L = lines_of(text)
    dates = {d.day: d for d in week_dates(today)}
    days, cur = {}, None
    hdr = re.compile(r"^(m[åa]n|tis[d]?|ons[d]?|tor[s]?|fre[d]?)\w*\s+(\d{1,2})/(\d{1,2})\s*(.*)$", re.I)
    for l in L:
        l = re.sub(r"\s*\d+\s*:-\s*$", "", l).strip()
        if not l:
            continue
        if re.match(r"^öppettider", l, re.I) and days:
            break
        m = hdr.match(l)
        if m:
            d, mo = int(m.group(2)), int(m.group(3))
            day = dates.get(d)
            cur = day.isoformat() if day and day.month == mo else None
            if cur:
                days[cur] = {"dishes": [], "extra": "Flera sallader varje dag"}
            l = m.group(4).strip()
            if not l or cur is None:
                continue
        if cur is None:
            continue
        hint = None
        if re.match(r"^veg\s*:", l, re.I):
            hint = "veg"; l = re.sub(r"^veg\s*:\s*", "", l, flags=re.I)
        days[cur]["dishes"].append({"t": classify(l, hint), "n": tidy(l)})
    days = {k: v for k, v in days.items() if v["dishes"]}
    if not days:
        raise ValueError("hittade inga rätter för denna vecka")
    return {"days": days}


def parse_bredbar(text, today):
    L = lines_of(text)
    want = f"{DAYS[today.weekday()]} {today.day} {MONTHS[today.month-1]}"
    if not any(l.lower() == want for l in L):
        raise ValueError("Dagens meny inte publicerad än")
    dishes, extra, mode, pending = [], [], None, None
    for i, l in enumerate(L):
        if not l:
            continue
        up = l.upper()
        if up == "DAGENS EXTRA":
            mode = "extra"; continue
        if up == "DAGENS RÄTTER":
            mode = "dish"; continue
        if re.match(r"^i lunchpriset", l, re.I) or up == "SALLADSMENY":
            break
        if mode == "tips":
            if re.match(r"^\d+\s*kr$", l):
                extra[-1] = extra[-1] + f", {l}"; mode = "dish"; continue
            extra.append("Tips: " + tidy(l)); continue
        if mode == "extra":
            extra.append("Dagens extra: " + tidy(re.sub(r"^idag bjuder vi på\s*", "", l, flags=re.I))); mode = None; continue
        if mode != "dish" or l.startswith("("):
            continue
        if re.fullmatch(r"vegetariskt?:?", l, re.I):
            pending = "veg"; continue
        if re.fullmatch(r"tips:?", l, re.I):
            mode = "tips"; continue
        if re.match(r"^tips", l, re.I):
            t = re.sub(r"^tips\s*:?\s*", "", l, flags=re.I)
            nxt = L[i+1].strip() if i + 1 < len(L) else ""
            extra.append("Tips: " + tidy(t) + (f", {nxt}" if re.match(r"^\d+\s*kr$", nxt) else ""))
            continue
        if re.match(r"^\d+\s*kr$", l):
            continue
        hint, pending = pending, None
        if re.match(r"^vegetarisk", l, re.I):
            hint = "veg"; l = re.sub(r"^vegetariskt?\s*:?\s*", "", l, flags=re.I)
        if not l:
            pending = hint; continue
        dishes.append({"t": classify(l, hint), "n": tidy(l)})
    if not dishes:
        raise ValueError("hittade inga rätter")
    out = {"dishes": dishes}
    if extra:
        out["extra"] = " ".join(e.rstrip(".") + "." for e in extra)
    return {"days": {today.isoformat(): out}}


def parse_kanpla(text, today):
    L = [l for l in lines_of(text) if l]
    want = f"{DAYS[today.weekday()]} {today.day}:e"
    try:
        i = next(k for k, l in enumerate(L) if l.lower() == want)
    except StopIteration:
        raise ValueError("Dagens meny syns inte än")
    cats = {"trädgården": "veg", "gården": "kott", "dagens tips!": None, "havet": "fisk"}
    dishes, extra = [], None
    k = i + 1
    if k < len(L) and L[k].lower() == "lunch":
        k += 1
    while k < len(L) and L[k] not in ("TKC Solna", "Få hjälp"):
        cat = L[k]; k += 1
        if k >= len(L): break
        name = L[k]; k += 1
        desc = ""
        if k < len(L) and not re.match(r"^\d+\s*kr$", L[k]) and L[k] not in ("TKC Solna",):
            desc = L[k]; k += 1
        if k < len(L) and re.match(r"^\d+\s*kr$", L[k]):
            k += 1
        txt = name + (" med " + desc[:1].lower() + desc[1:] if desc else "")
        hint = cats.get(cat.lower())
        d = {"t": classify(txt, hint), "n": tidy(txt)}
        dishes.append(d)
        if cat.lower().startswith("dagens tips"):
            extra = "Dagens tips: " + name[:1].lower() + name[1:]
    if not dishes:
        raise ValueError("hittade inga rätter")
    out = {"dishes": dishes}
    if extra: out["extra"] = extra
    return {"days": {today.isoformat(): out}}


PARSERS = {"bredbar": parse_bredbar, "matronan": parse_matronan, "bramat": parse_bramat,
           "greven": parse_greven, "kitchenclub": parse_kanpla}
DAILY = {"bredbar", "kitchenclub"}


# ---------- hämtning ----------
def fetch_text(key):
    url = SOURCES[key]
    if key == "kitchenclub":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(locale="sv-SE", user_agent=UA)
            pg.goto(url, wait_until="networkidle", timeout=60000)
            pg.wait_for_timeout(3000)
            t = pg.inner_text("body"); b.close(); return t
    import requests
    from bs4 import BeautifulSoup
    import time
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "sv-SE,sv"}, timeout=30)
            r.raise_for_status()
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    html = r.content.decode("utf-8", errors="replace")
    s = BeautifulSoup(html, "lxml")
    for tag in s(["script", "style", "noscript", "svg"]): tag.decompose()
    t = s.get_text("\n")
    if "Blocked Page" in t[:500]:
        raise ValueError("sidan blockerade läsningen")
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline")
    ap.add_argument("--date")
    a = ap.parse_args()
    now = dt.datetime.now(TZ)
    today = dt.date.fromisoformat(a.date) if a.date else now.date()
    if today.weekday() > 4:
        print("Helg – inget att göra."); return 0
    week = {d.isoformat() for d in week_dates(today)}
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {"restaurants": {}}
    res, report = {}, []
    for key, parser in PARSERS.items():
        prev = old.get("restaurants", {}).get(key, {})
        prev_days = {k: v for k, v in prev.get("days", {}).items() if k in week}
        try:
            text = open(os.path.join(a.offline, key + ".txt"), encoding="utf-8").read() if a.offline else fetch_text(key)
            got = parser(text, today)
            days = dict(prev_days) if key in DAILY else {}
            days.update(got["days"])
            res[key] = {"status": "ok", "days": dict(sorted(days.items()))}
            report.append(f"{key}: ok ({len(got['days'].get(today.isoformat(), {}).get('dishes', []))} rätter i dag)")
        except Exception as e:
            res[key] = {"status": "fel", "note": str(e)[:120], "days": prev_days}
            report.append(f"{key}: FEL – {e}")
    new = {"updated": now.isoformat(timespec="seconds"), "week": today.isocalendar()[1], "restaurants": res}
    strip = lambda d: {k: v for k, v in d.items() if k != "updated"}
    changed = strip(new) != strip(old)
    if changed or not a.offline:
        json.dump(new, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n".join(report))
    print("ändrad" if changed else "oförändrad")
    return 0


if __name__ == "__main__":
    sys.exit(main())
