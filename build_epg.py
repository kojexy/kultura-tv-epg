"""Собирает компактную программу передач для приложения «Культурное ТВ».

Источники:
  * iptvx.one — полный XMLTV (около 90 МБ) с описаниями передач;
  * epg.pw — китайские каналы CCTV (только названия), в приложении их id пишутся как "pw:<номер>".
Результат: epg.json с передачами наших каналов на ближайшие ~40 часов. Всё, что не на русском,
переводится на русский; оригинальное название сохраняется в поле "o".
"""
import gzip, json, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from datetime import datetime

SOURCE = "https://iptvx.one/EPG"
PW_SOURCE = "https://epg.pw/xmltv/epg_CN.xml.gz"

# id каналов в iptvx.one, которые использует приложение (поле epgId в Channels.kt).
CHANNELS = {
    # Россия
    "kultura", "kultura-pl7", "smotrim-100-klassika", "smotrim-100-fakty", "smotrim-100-detskoe",
    "doverie", "zvezda", "otr", "mir", "sankt-peterburg", "bolshaya-azia", "prosveschenie",
    "karusel", "spas", "soyuz", "rossia-24", "mir-24", "izvestia", "rbk", "360-news",
    # Беларусь, Польша, Чехия
    "belarus1", "belarus2", "belarus3", "belarus24",
    "tvp-kultura", "tvp-historia", "tvp1", "tvp2", "tvp-abc", "stopklatka-pl", "fokus-pl",
    "ct2-cz",
    # Остальной мир
    "cgtn", "cctv4", "nhk-world", "arirang", "deutsche-welle-ru", "deutsche-welle-eu", "euronews",
    "france24-fr", "france24-en", "trt-1-hd", "trt-muzik-tr",
}
# epg.pw: каналы CCTV, CGTN Documentary и провинциальные каналы Китая.
PW_CHANNELS = {
    "545932", "545934", "545938", "545942", "545943", "545945", "545946", "545947", "544390",
    # CCTV-2, 7, 8, 9, 12, 17 и провинциальные каналы
    "545933", "545939", "545940", "545941", "545944", "561325",
    "545959", "545960", "545966", "545990", "539766", "539742", "539679",
}

CACHE = "translations.json"

def needs_translation(channel, text):
    """Белорусские каналы переводим всегда, остальное — если в тексте почти нет кириллицы."""
    if channel.startswith("belarus"):
        return True
    letters = re.findall(r"[^\W\d_]", text)
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return bool(letters) and cyr / len(letters) < 0.5

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
    texts = {x[k] for ch, items in out.items() for x in items for k in ("t", "d")
             if k in x and needs_translation(ch, x[k])}
    todo = [t for t in texts if t not in cache]
    # По одной строке на запрос: пакетный перевод иногда сдвигает строки,
    # и описание уезжает к чужой передаче. Благодаря кэшу новых строк за запуск немного.
    with ThreadPoolExecutor(4) as ex:
        for src, ru in zip(todo, ex.map(_gtx, todo)):
            if ru:
                cache[src] = ru
    # Google иногда отвечает отказом на частые запросы; пропущенное добираем медленно, по одному.
    for src in [t for t in todo if t not in cache][:300]:
        time.sleep(1.5)
        ru = _gtx(src)
        if ru:
            cache[src] = ru
    for items in out.values():
        for x in items:
            if x["t"] in texts and x["t"] in cache and cache[x["t"]] != x["t"]:
                x["o"] = x["t"]
                x["t"] = cache[x["t"]]
            if "d" in x and x["d"] in texts and x["d"] in cache:
                x["d"] = cache[x["d"]]
    # В кэше оставляем только то, что ещё пригодится, иначе он будет бесконечно расти.
    json.dump({t: cache[t] for t in texts if t in cache}, open(CACHE, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"new texts {len(todo)}, translated total {sum(1 for t in texts if t in cache)} of {len(texts)}")

def ts(s):
    return int(datetime.strptime(s, "%Y%m%d%H%M%S %z").timestamp())

def collect(path, wanted, prefix, out, lo, hi):
    with gzip.open(path) as f:
        for _, el in ET.iterparse(f):
            if el.tag == "programme":
                ch = el.get("channel")
                if ch in wanted:
                    s, e = ts(el.get("start")), ts(el.get("stop"))
                    if e > lo and s < hi:
                        item = {"s": s, "e": e, "t": " ".join((el.findtext("title") or "").split())}
                        d = " ".join((el.findtext("desc") or "").split())
                        if d:
                            item["d"] = d[:700]
                        out.setdefault(prefix + ch, []).append(item)
                el.clear()
            elif el.tag == "channel":
                el.clear()

def main(path, pw_path=None):
    now = time.time()
    lo, hi = now - 4 * 3600, now + 40 * 3600
    out = {}
    collect(path, CHANNELS, "", out, lo, hi)
    if pw_path:
        collect(pw_path, PW_CHANNELS, "pw:", out, lo, hi)
    for v in out.values():
        v.sort(key=lambda x: x["s"])
    translate_all(out)
    data = {"updated": int(now), "channels": {k: v for k, v in out.items() if v}}
    with open("epg.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print({k: len(v) for k, v in sorted(out.items())})

def download(url, path, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        download(SOURCE, "source.xml.gz", "KulturaTV-EPG/1.0")
        pw = None
        try:
            download(PW_SOURCE, "pw.xml.gz", "Mozilla/5.0")
            pw = "pw.xml.gz"
        except Exception as e:
            print("epg.pw недоступен:", e)
        main("source.xml.gz", pw)
