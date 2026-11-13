#!/usr/bin/env python3
# يجمع أخبار آخن من مصادر RSS مجانية، يترجمها للعربية، ويكتب data/news.json
# يعمل على GitHub Actions كل ساعة — بلا مفاتيح ولا اشتراكات.
import email.utils, html, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "news.json")
MAX_AGE = 4 * 24 * 3600      # أخبار آخر 4 أيام
MAX_PER_CAT = 30
UA = {"User-Agent": "Mozilla/5.0 (AachenNews; +https://github.com/iphonesalha-web/aachen-news-ios)"}


def gnews(q, lang="de"):
    gl = "DE"
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:4d")
            + f"&hl={lang}&gl={gl}&ceid={gl}:{lang}")


# الترتيب مهم: الخبر المكرر يبقى في أول قسم ظهر فيه
FEEDS = [
    ("security", "Polizei Aachen", "https://www.presseportal.de/rss/dienststelle_11559.rss2"),
    ("security", None, gnews('Aachen (Polizei OR Feuerwehr OR Unfall OR Festnahme OR Brand OR Messer OR Einbruch)')),
    ("community", None, gnews('آخن', "ar")),
    ("community", None, gnews('Aachen (Syrer OR Araber OR arabisch OR Moschee OR Geflüchtete OR Migranten OR Libanon OR Ausländerbehörde)')),
    ("city", None, gnews('Aachen')),
    ("city", None, gnews('"Städteregion Aachen" OR Alsdorf OR Würselen OR Herzogenrath OR Eschweiler OR Stolberg')),
]


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_time(s):
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except Exception:
        return None


def is_arabic(s):
    return len(re.findall(r"[؀-ۿ]", s)) > len(s) * 0.3


def _ms_translate(texts):
    """مترجم Microsoft المجاني الذي يستعمله متصفح Edge — يقبل دفعة نصوص في طلب واحد."""
    token = fetch("https://edge.microsoft.com/translate/auth", 15).decode().strip()
    out = []
    for i in range(0, len(texts), 50):
        chunk = texts[i:i + 50]
        req = urllib.request.Request(
            "https://api-edge.cognitive.microsofttranslator.com/translate?api-version=3.0&to=ar",
            data=json.dumps([{"Text": t[:1000]} for t in chunk]).encode(),
            headers={**UA, "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        out += [x["translations"][0]["text"] for x in res]
        time.sleep(1)
    return out


def _google_translate(text):
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ar&dt=t&q="
           + urllib.parse.quote(text[:1500]))
    data = json.loads(fetch(url, 15))
    return "".join(part[0] for part in data[0] if part and part[0])


def translate_many(texts):
    """يعيد قاموس {النص: الترجمة}. ما يفشل يبقى بلا ترجمة ويُعاد في المرة القادمة."""
    todo = [t for t in dict.fromkeys(texts) if t and not is_arabic(t)]
    done = {t: t for t in texts if t and is_arabic(t)}
    if not todo:
        return done
    try:
        done.update(zip(todo, _ms_translate(todo)))
        print(f"translated {len(todo)} via Microsoft")
        return done
    except Exception as e:
        print("microsoft translate failed:", e, file=sys.stderr)
    fails = 0
    for t in todo:
        try:
            done[t] = _google_translate(t)
            fails = 0
            time.sleep(0.5)
        except Exception as e:
            fails += 1
            print("google translate failed:", e, file=sys.stderr)
            if fails >= 2:
                break
    return done


def norm_title(t):
    return re.sub(r"[^\w]+", "", t.lower())[:80]


def read_feed(cat, fixed_source, url):
    items = []
    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:
        print(f"feed failed {url}: {e}", file=sys.stderr)
        return items
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        ts = parse_time(it.findtext("pubDate") or "")
        src_el = it.find("source")
        feed_src = src_el.text.strip() if src_el is not None and src_el.text else ""
        source = fixed_source or feed_src
        # Google News يضيف " - المصدر" في آخر العنوان
        if feed_src and title.endswith(" - " + feed_src):
            title = title[: -len(" - " + feed_src)]
        summary = "" if "news.google.com" in url else clean(it.findtext("description"))[:400]
        if title and link:
            items.append({"cat": cat, "title_de": title, "summary_de": summary, "url": link,
                          "source": source or urllib.parse.urlparse(link).netloc, "ts": ts})
    return items


def main():
    try:
        with open(OUT) as f:
            old = {i["url"]: i for i in json.load(f).get("items", []) if i.get("url")}
    except Exception:
        old = {}

    now = time.time()
    seen_titles, seen_urls, result = set(), set(), []
    counts = {}
    for cat, src, url in FEEDS:
        for it in read_feed(cat, src, url):
            if it["ts"] and now - it["ts"] > MAX_AGE:
                continue
            key = norm_title(it["title_de"])
            if it["url"] in seen_urls or key in seen_titles:
                continue
            if counts.get(cat, 0) >= MAX_PER_CAT:
                continue
            seen_urls.add(it["url"]); seen_titles.add(key)
            counts[cat] = counts.get(cat, 0) + 1
            prev = old.get(it["url"])
            cached = (prev and prev.get("title_orig") == it["title_de"]
                      and is_arabic(prev.get("title", "")))
            result.append({
                "cat": cat,
                "title": prev["title"] if cached else it["title_de"],
                "summary": prev.get("summary", "") if cached else it["summary_de"],
                "title_orig": it["title_de"],
                "source": it["source"],
                "url": it["url"],
                "time": datetime.fromtimestamp(it["ts"], timezone.utc).isoformat() if it["ts"] else None,
                "_new": not cached,
            })

    fresh = [r for r in result if r["_new"]]
    tr = translate_many([r["title"] for r in fresh] + [r["summary"] for r in fresh if r["summary"]])
    for r in result:
        if r.pop("_new"):
            r["title"] = tr.get(r["title"], r["title"])
            r["summary"] = tr.get(r["summary"], r["summary"]) if r["summary"] else ""

    result.sort(key=lambda i: i["time"] or "", reverse=True)
    if not result and old:
        print("no items fetched — keeping old file", file=sys.stderr)
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "items": result}, f, ensure_ascii=False, indent=1)
    print("items:", counts)


if __name__ == "__main__":
    main()
