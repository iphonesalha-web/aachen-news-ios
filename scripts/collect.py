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
MAX_PER_FEED = 12      # حتى لا يطغى مصدر واحد على القسم
CAT_LIMIT = {"papers": 48}   # الصحف: 6 أخبار من كل صحيفة
FEED_LIMIT = {"papers": 6}
UA = {"User-Agent": "Mozilla/5.0 (AachenNews; +https://github.com/iphonesalha-web/aachen-news-ios)"}


def gnews(q, lang="de", gl="DE"):
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
    # ألمانيا: أهم العناوين الألمانية (تُترجم) + أخبار عربية عن ألمانيا
    ("germany", None, "https://news.google.com/rss?hl=de&gl=DE&ceid=DE:de"),
    ("germany", None, gnews("ألمانيا", "ar")),
    # التقنية والذكاء الاصطناعي
    ("tech", None, gnews('"الذكاء الاصطناعي" OR ChatGPT OR OpenAI OR Claude OR Gemini', "ar")),
    ("tech", None, gnews('"künstliche Intelligenz" OR KI-Modell OR OpenAI OR Anthropic OR "Google Gemini" OR ChatGPT')),
    ("tech", None, gnews('iPhone OR Apple OR Android OR Samsung OR Smartphone OR Tech')),
    # الصحف الألمانية الكبرى (عناوينها الرئيسية مع ملخص، تُترجم)
    ("papers", "Tagesschau", "https://www.tagesschau.de/index~rss2.xml"),
    ("papers", "Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss"),
    ("papers", "Zeit", "https://newsfeed.zeit.de/index"),
    ("papers", "FAZ", "https://www.faz.net/rss/aktuell/"),
    ("papers", "Süddeutsche", "https://rss.sueddeutsche.de/rss/Topthemen"),
    ("papers", "WELT", "https://www.welt.de/feeds/topnews.rss"),
    ("papers", "n-tv", "https://www.n-tv.de/rss"),
    # لبنان: أهم العناوين في لبنان بالعربية
    ("lebanon", None, "https://news.google.com/rss?hl=ar&gl=LB&ceid=LB:ar"),
    ("lebanon", None, gnews("لبنان", "ar", "LB")),
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


def _post(url, form, timeout=30):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(),
                                 headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _g_api(text):
    data = json.loads(_post("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ar&dt=t",
                            {"q": text}))
    return "".join(part[0] for part in data[0] if part and part[0])


def _g_dict(text):
    data = json.loads(_post("https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl=ar",
                            {"q": text}))
    first = data[0]
    return first[0] if isinstance(first, list) else first


def _g_mobile(text):
    page = fetch("https://translate.google.com/m?sl=auto&tl=ar&hl=ar&q=" + urllib.parse.quote(text), 30).decode()
    m = re.search(r'class="result-container">(.*?)</div>', page, re.S)
    if not m:
        raise ValueError("no result")
    return html.unescape(m.group(1))


ENGINES = [("google-api", _g_api), ("google-dict", _g_dict), ("google-mobile", _g_mobile)]


def _chunks(texts, limit=1800):
    cur, size = [], 0
    for t in texts:
        if cur and size + len(t) + 1 > limit:
            yield cur
            cur, size = [], 0
        cur.append(t)
        size += len(t) + 1
    if cur:
        yield cur


def translate_many(texts):
    """يعيد قاموس {النص: الترجمة}. يرسل النصوص دفعات (سطر لكل نص) لتقليل الطلبات.
    ما يفشل يبقى بلا ترجمة ويُعاد في المرة القادمة."""
    todo = [t.replace("\n", " ") for t in dict.fromkeys(texts) if t and not is_arabic(t)]
    done = {t: t for t in texts if t and is_arabic(t)}
    engines = list(ENGINES)
    for chunk in _chunks(todo):
        while engines:
            name, fn = engines[0]
            try:
                lines = fn("\n".join(chunk)).split("\n")
                if len(lines) == len(chunk):
                    done.update(zip(chunk, (l.strip() for l in lines)))
                else:  # لم يحافظ على الأسطر — نترجم واحداً واحداً
                    for t in chunk:
                        done[t] = fn(t).strip()
                        time.sleep(0.4)
                print(f"translated {len(chunk)} via {name}")
                time.sleep(1.5)
                break
            except Exception as e:
                print(f"{name} failed:", e, file=sys.stderr)
                engines.pop(0)
        if not engines:
            break
    return done


STOP = {"aachen", "aachener", "nach", "eine", "einer", "einem", "einen", "der", "die", "das", "und",
        "mit", "von", "für", "auf", "dem", "den", "ist", "sind", "wird", "werden", "nicht", "auch",
        "über", "beim", "bei", "aus", "was", "wie", "noch", "bislang", "bekannt", "heute"}


def words(t):
    return {w for w in re.findall(r"\w{4,}", t.lower()) if w not in STOP}


def similar(a, b):
    if len(a) < 2 or len(b) < 2:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.5


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
        taken = 0
        for it in read_feed(cat, src, url):
            if taken >= FEED_LIMIT.get(cat, MAX_PER_FEED):
                break
            if it["ts"] and now - it["ts"] > MAX_AGE:
                continue
            key = norm_title(it["title_de"])
            if it["url"] in seen_urls or key in seen_titles:
                continue
            w = words(it["title_de"])
            dup = next((r for r in result if similar(w, r["_w"])), None)
            if dup:  # نفس الخبر من مصدر آخر
                dup["more"] = dup.get("more", 0) + 1
                seen_urls.add(it["url"]); seen_titles.add(key)
                continue
            if counts.get(cat, 0) >= CAT_LIMIT.get(cat, MAX_PER_CAT):
                continue
            seen_urls.add(it["url"]); seen_titles.add(key)
            counts[cat] = counts.get(cat, 0) + 1
            taken += 1
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
                "_w": w,
            })

    fresh = [r for r in result if r["_new"]]
    tr = translate_many([r["title"] for r in fresh] + [r["summary"] for r in fresh if r["summary"]])
    for r in result:
        r.pop("_w", None)
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
    notify(result)


# ---------- تنبيهات تيليجرام للأخبار المهمة ----------
NOTIFIED = os.path.join(ROOT, "data", "notified.json")
NOTIFY_CATS = {
    "security": "🚨 آخن — أمن وحوادث",
    "community": "👥 آخن — الجالية العربية",
    "city": "🏙️ آخن — المدينة",
    "germany": "🇩🇪 ألمانيا",
    "papers": "📰 الصحف الألمانية",
    "lebanon": "🇱🇧 لبنان",
    "tech": "🤖 تقنية وذكاء اصطناعي",
}
# أقصى عدد رسائل لكل قسم في كل تشغيل (كل ساعة) حتى لا تمتلئ المحادثة
NOTIFY_LIMIT = {"security": 6, "community": 4, "city": 4, "germany": 3, "papers": 4, "lebanon": 3, "tech": 3}
NOTIFY_MAX_AGE = 12 * 3600     # لا ننبّه عن خبر أقدم من 12 ساعة


def tg_send(token, chat, text):
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
        return json.loads(r.read()).get("ok")


def notify(items):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat:
        print("telegram: no secrets — skipped")
        return
    first_run = not os.path.exists(NOTIFIED)
    try:
        with open(NOTIFIED) as f:
            st = json.load(f)
    except Exception:
        st = {}
    if isinstance(st, list):   # الصيغة القديمة: كانت للأمن والجالية فقط
        st = {"sent": st, "cats": ["security", "community"]}
    sent = st.get("sent", [])
    known_cats = set(st.get("cats", []))

    sent_words = [set(x.get("w", [])) for x in sent]
    sent_urls = {x.get("url") for x in sent}
    now = time.time()
    fresh = {}
    for it in items:
        cat = it["cat"]
        if cat not in NOTIFY_CATS or it["url"] in sent_urls:
            continue
        w = words(it.get("title_orig") or it["title"])
        if any(similar(w, sw) for sw in sent_words):
            continue
        sent.append({"url": it["url"], "w": sorted(w), "at": int(now)})
        sent_words.append(w)
        sent_urls.add(it["url"])
        if first_run or cat not in known_cats:
            continue   # قسم جديد: نعتبر أخباره الحالية قديمة بلا إرسال
        try:
            age = now - datetime.fromisoformat(it["time"]).timestamp() if it.get("time") else 0
        except ValueError:
            age = 0
        if age <= NOTIFY_MAX_AGE:
            fresh.setdefault(cat, []).append(it)

    count = 0
    try:
        if first_run:
            tg_send(token, chat, "✅ تم ربط تطبيق أخبار آخن بهذا البوت.\n"
                                 "ستصلك هنا الأخبار الجديدة من كل الأقسام فور نشرها.")
        for cat in NOTIFY_CATS:
            for it in sorted(fresh.get(cat, []), key=lambda i: i.get("time") or "")[-NOTIFY_LIMIT[cat]:]:
                more = f" (+{it['more']} مصادر)" if it.get("more") else ""
                tg_send(token, chat, f"{NOTIFY_CATS[cat]}\n\n{it['title']}\n\n"
                                     f"📰 {it['source']}{more}\n{it['url']}")
                count += 1
                time.sleep(1.1)
        print(f"telegram: sent {count}")
    except Exception as e:
        print("telegram failed:", e, file=sys.stderr)
        return   # لا نحفظ — نحاول في المرة القادمة

    with open(NOTIFIED, "w") as f:
        json.dump({"cats": sorted(set(NOTIFY_CATS)), "sent": sent[-1500:]}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
