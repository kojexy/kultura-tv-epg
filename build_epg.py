"""Собирает компактную программу передач для приложения «Культурное ТВ».

Источник: полный XMLTV iptvx.one (около 90 МБ, с описаниями передач).
Результат: epg.json с передачами наших каналов на ближайшие ~36 часов.
"""
import gzip, json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from datetime import datetime

SOURCE = "https://iptvx.one/EPG"

# id каналов в iptvx.one, которые использует приложение (поле epgId в Channels.kt).
CHANNELS = {
    "kultura", "kultura-pl7", "smotrim-100-klassika", "smotrim-100-fakty", "smotrim-100-detskoe",
    "doverie", "zvezda", "otr", "mir", "sankt-peterburg", "bolshaya-azia", "prosveschenie",
    "karusel", "spas", "soyuz",
    "belarus1", "belarus2", "belarus3", "belarus24",
    "tvp-kultura", "tvp-historia", "tvp1", "tvp2", "tvp-abc", "stopklatka-pl", "fokus-pl",
    "ct2-cz",
}

# Иностранные каналы: названия и описания переводим на русский (оригинал названия сохраняем в "o").
FOREIGN = {
    "belarus1", "belarus2", "belarus3", "belarus24",
    "tvp-kultura", "tvp-historia", "tvp1", "tvp2", "tvp-abc", "stopklatka-pl", "fokus-pl",
    "ct2-cz",
}
CACHE = "translations.json"

def _gtx(text):
    """Бесплатный веб-переводчик Google; при ошибке возвращает None и текст остаётся как есть."""
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ru&dt=t"
    body = urllib.parse.urlencode({"q": text}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                parts = json.load(r)[0]
            return "".join(p[0] for p in parts if p and p[0])
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None

def translate_all(out):
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    texts = {x[k] for ch in FOREIGN for x in out.get(ch, []) for k in ("t", "d") if k in x}
    todo = [t for t in texts if t not in cache]
    # По одной строке на запрос: пакетный перевод иногда сдвигает строки,
    # и описание уезжает к чужой передаче. Благодаря кэшу новых строк за запуск немного.
    with ThreadPoolExecutor(4) as ex:
        for src, ru in zip(todo, ex.map(_gtx, todo)):
            if ru:
                cache[src] = ru
    for ch in FOREIGN:
        for x in out.get(ch, []):
            if x["t"] in cache and cache[x["t"]] != x["t"]:
                x["o"] = x["t"]
                x["t"] = cache[x["t"]]
            if "d" in x and x["d"] in cache:
                x["d"] = cache[x["d"]]
    # В кэше оставляем только то, что ещё пригодится, иначе он будет бесконечно расти.
    json.dump({t: cache[t] for t in texts if t in cache}, open(CACHE, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"translated {len(todo)} new texts, cache {len(texts)}")

def ts(s):
    return int(datetime.strptime(s, "%Y%m%d%H%M%S %z").timestamp())

def main(path):
    now = time.time()
    lo, hi = now - 4 * 3600, now + 40 * 3600
    out = {c: [] for c in CHANNELS}
    with gzip.open(path) as f:
        for _, el in ET.iterparse(f):
            if el.tag == "programme":
                ch = el.get("channel")
                if ch in CHANNELS:
                    s, e = ts(el.get("start")), ts(el.get("stop"))
                    if e > lo and s < hi:
                        item = {"s": s, "e": e, "t": " ".join((el.findtext("title") or "").split())}
                        d = " ".join((el.findtext("desc") or "").split())
                        if d:
                            item["d"] = d[:700]
                        out[ch].append(item)
                el.clear()
            elif el.tag == "channel":
                el.clear()
    for v in out.values():
        v.sort(key=lambda x: x["s"])
    translate_all(out)
    data = {"updated": int(now), "channels": {k: v for k, v in out.items() if v}}
    with open("epg.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print({k: len(v) for k, v in out.items()})

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        req = urllib.request.Request(SOURCE, headers={"User-Agent": "KulturaTV-EPG/1.0"})
        with urllib.request.urlopen(req, timeout=600) as r, open("source.xml.gz", "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        main("source.xml.gz")
