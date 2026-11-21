#!/usr/bin/env python3
# يجمع أخبار آخن من مصادر RSS مجانية، يترجمها للعربية، ويكتب data/news.json
# يعمل على GitHub Actions كل ساعة — بلا مفاتيح ولا اشتراكات.
import email.utils, html, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

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


def tg_send(token, chat, text, parse_mode=None):
    params = {"chat_id": chat, "text": text, "disable_web_page_preview": "true"}
    if parse_mode:
        params["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(params).encode()
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
                esc = lambda x: html.escape(str(x or ""), quote=False)
                # الرابط مخفي خلف كلمة «اقرأ الخبر» بدل عرضه كاملاً (روابط Google News طويلة جداً)
                tg_send(token, chat,
                        f"<b>{esc(NOTIFY_CATS[cat])}</b>\n\n{esc(it['title'])}\n\n"
                        f"📰 {esc(it['source'])}{esc(more)} · "
                        f"<a href=\"{html.escape(it['url'], quote=True)}\">اقرأ الخبر</a>",
                        parse_mode="HTML")
                count += 1
                time.sleep(1.1)
        print(f"telegram: sent {count}")
    except Exception as e:
        print("telegram failed:", e, file=sys.stderr)
        return   # لا نحفظ — نحاول في المرة القادمة

    with open(NOTIFIED, "w") as f:
        json.dump({"cats": sorted(set(NOTIFY_CATS)), "sent": sent[-1500:]}, f, ensure_ascii=False)


# ---------- الطقس: تقرير صباحي + تحذيرات رسمية (Open-Meteo و DWD عبر Bright Sky — مجانية بلا مفاتيح) ----------
WEATHER_STATE = os.path.join(ROOT, "data", "weather_state.json")
LAT, LON = 50.7753, 6.0839            # آخن
REPORT_FROM_HOUR = 7                  # أول تقرير يومي بعد الساعة 7 صباحاً بتوقيت ألمانيا
ALERT_HOURS = range(6, 23)            # لا تنبيهات ليلاً
MIN_SEVERITY = {"moderate", "severe", "extreme"}
AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
WMO = {
    0: ("صافٍ", "☀️"), 1: ("غالباً صافٍ", "🌤️"), 2: ("غائم جزئياً", "⛅"), 3: ("غائم", "☁️"),
    45: ("ضباب", "🌫️"), 48: ("ضباب متجمد", "🌫️"),
    51: ("رذاذ خفيف", "🌦️"), 53: ("رذاذ", "🌦️"), 55: ("رذاذ كثيف", "🌧️"),
    56: ("رذاذ متجمد", "🌧️"), 57: ("رذاذ متجمد كثيف", "🌧️"),
    61: ("مطر خفيف", "🌧️"), 63: ("مطر متوسط", "🌧️"), 65: ("مطر غزير", "🌧️"),
    66: ("مطر متجمد", "🌧️"), 67: ("مطر متجمد غزير", "🌧️"),
    71: ("ثلج خفيف", "🌨️"), 73: ("ثلج متوسط", "🌨️"), 75: ("ثلج كثيف", "❄️"), 77: ("حبيبات ثلج", "❄️"),
    80: ("زخات مطر خفيفة", "🌦️"), 81: ("زخات مطر", "🌧️"), 82: ("زخات مطر غزيرة", "⛈️"),
    85: ("زخات ثلج", "🌨️"), 86: ("زخات ثلج كثيفة", "❄️"),
    95: ("عاصفة رعدية", "⛈️"), 96: ("عاصفة رعدية مع برَد", "⛈️"), 99: ("عاصفة رعدية قوية مع برَد", "⛈️"),
}


def ltr(x):
    """يعزل الأرقام والوحدات حتى لا ينقلب ترتيبها داخل النص العربي."""
    return "⁦" + str(x) + "⁩"


def wmo(code):
    return WMO.get(code, ("غير معروف", "🌡️"))


def berlin_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Berlin"))


def fetch_retry(url, tries=4):
    """إعادة محاولة عند أعطال الشبكة العابرة (مثل انتهاء مهلة SSL)."""
    for i in range(tries):
        try:
            return fetch(url, 40)
        except Exception as e:
            print(f"weather fetch retry {i + 1}: {e}", file=sys.stderr)
            if i == tries - 1:
                raise
            time.sleep(4 * (i + 1))


def fetch_forecast_openmeteo():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
           "precipitation_sum,wind_gusts_10m_max,sunrise,sunset"
           "&timezone=Europe%%2FBerlin&forecast_days=3" % (LAT, LON))
    return json.loads(fetch_retry(url, tries=2))


# أيقونات Bright Sky (DWD) ← رموز WMO المستعملة في التقرير، مرتبة من الأخف إلى الأشد
BS_ICON = {"clear-day": 0, "clear-night": 0, "partly-cloudy-day": 2, "partly-cloudy-night": 2,
           "cloudy": 3, "wind": 3, "fog": 45, "rain": 63, "sleet": 66, "snow": 73,
           "hail": 96, "thunderstorm": 95}
BS_RANK = {0: 0, 2: 1, 3: 2, 45: 3, 63: 4, 66: 5, 73: 6, 96: 7, 95: 8}


def fetch_forecast_brightsky():
    """احتياطي مجاني (بيانات DWD) إذا رفض Open-Meteo الطلب. لا يوفّر احتمال المطر ولا الشروق/الغروب."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Berlin")
    start = berlin_now().date()
    end = start + timedelta(days=2)
    cur = json.loads(fetch_retry("https://api.brightsky.dev/current_weather?lat=%s&lon=%s" % (LAT, LON)))["weather"]
    rows = json.loads(fetch_retry("https://api.brightsky.dev/weather?lat=%s&lon=%s&date=%s&last_date=%s&tz=Europe/Berlin"
                                  % (LAT, LON, start.isoformat(), (end + timedelta(days=1)).isoformat())))["weather"]
    days = {}
    for r in rows:
        d = datetime.fromisoformat(r["timestamp"]).astimezone(tz).date().isoformat()
        days.setdefault(d, []).append(r)
    daily = {k: [] for k in ("time", "weather_code", "temperature_2m_max", "temperature_2m_min",
                             "precipitation_probability_max", "precipitation_sum", "wind_gusts_10m_max")}
    for i in range(3):
        d = (start + timedelta(days=i)).isoformat()
        rs = days.get(d, [])
        temps = [r["temperature"] for r in rs if r.get("temperature") is not None]
        codes = [BS_ICON.get(r.get("icon"), 3) for r in rs]
        daily["time"].append(d)
        daily["weather_code"].append(max(codes, key=lambda c: BS_RANK.get(c, 0)) if codes else 3)
        daily["temperature_2m_max"].append(max(temps) if temps else 0)
        daily["temperature_2m_min"].append(min(temps) if temps else 0)
        daily["precipitation_probability_max"].append(None)
        daily["precipitation_sum"].append(sum(r.get("precipitation") or 0 for r in rs))
        daily["wind_gusts_10m_max"].append(max([r.get("wind_gust_speed") or 0 for r in rs] or [0]))
    return {"current": {"temperature_2m": cur.get("temperature") or 0, "apparent_temperature": None,
                        "weather_code": BS_ICON.get(cur.get("icon"), 3), "wind_speed_10m": cur.get("wind_speed_10")},
            "daily": daily}


def fetch_forecast():
    try:
        return fetch_forecast_openmeteo()
    except Exception as e:
        print("weather: open-meteo failed, using brightsky:", e, file=sys.stderr)
        return fetch_forecast_brightsky()


def fetch_dwd_alerts():
    url = "https://api.brightsky.dev/alerts?lat=%s&lon=%s" % (LAT, LON)
    return json.loads(fetch_retry(url)).get("alerts", [])


def day_line(d, i, label):
    code = d["weather_code"][i]
    desc, icon = wmo(code)
    rain = d["precipitation_probability_max"][i]
    mm = d["precipitation_sum"][i]
    gust = d["wind_gusts_10m_max"][i]
    parts = [f"{icon} <b>{label}:</b> {desc}",
             f"العظمى {ltr(round(d['temperature_2m_max'][i]))}° · الصغرى {ltr(round(d['temperature_2m_min'][i]))}°"]
    if rain is not None and rain >= 20:
        extra = f" (≈{ltr(round(mm, 1))} mm)" if mm and mm >= 0.5 else ""
        parts.append(f"☔ احتمال المطر {ltr(str(rain) + '%')}{extra}")
    if gust is not None and gust >= 50:
        parts.append(f"💨 هبّات رياح حتى {ltr(str(round(gust)) + ' km/h')}")
    return "\n".join(parts)


def build_report(fc, now):
    cur, d = fc["current"], fc["daily"]
    desc, icon = wmo(cur["weather_code"])
    wd = AR_DAYS[now.weekday()]
    lines = [f"🌤️ <b>طقس آخن — {wd} {ltr(now.strftime('%d/%m'))}</b>", "",
             f"{icon} الآن: {ltr(str(round(cur['temperature_2m'])) + '°C')}"
             + (f" (المحسوس {ltr(str(round(cur['apparent_temperature'])) + '°C')})"
                if cur.get("apparent_temperature") is not None else "") + f" — {desc}", "",
             day_line(d, 0, "اليوم"), "", day_line(d, 1, "غداً")]
    try:
        sr, ss = d["sunrise"][0][-5:], d["sunset"][0][-5:]
        lines += ["", f"🌅 الشروق {ltr(sr)} · 🌇 الغروب {ltr(ss)}"]
    except Exception:
        pass
    return "\n".join(lines)


def forecast_alerts(fc):
    """تنبيهات من التوقعات نفسها (احتياط إذا لم تصدر تحذيرات رسمية): يُعاد [(مفتاح, نص)]"""
    d, out = fc["daily"], []
    for i, label in ((0, "اليوم"), (1, "غداً")):
        date = d["time"][i]
        code = d["weather_code"][i]
        gust = d["wind_gusts_10m_max"][i] or 0
        lo, hi = d["temperature_2m_min"][i], d["temperature_2m_max"][i]
        if code in (95, 96, 99):
            out.append((f"{date}:storm", f"⛈️ {label}: عاصفة رعدية متوقعة في آخن"))
        if code in (56, 57, 66, 67):
            out.append((f"{date}:freezing-rain", f"🧊 {label}: مطر متجمد — خطر انزلاق على الطرقات"))
        if code in (75, 86) or (code in (73, 85) and (d["precipitation_sum"][i] or 0) >= 5):
            out.append((f"{date}:snow", f"❄️ {label}: ثلوج كثيفة متوقعة"))
        if code in (65, 82) or (d["precipitation_sum"][i] or 0) >= 20:
            out.append((f"{date}:heavy-rain", f"🌧️ {label}: أمطار غزيرة متوقعة"))
        if gust >= 75:
            out.append((f"{date}:wind", f"💨 {label}: رياح عاصفة — هبّات حتى {ltr(str(round(gust)) + ' km/h')}"))
        if lo is not None and lo <= -3:
            out.append((f"{date}:frost", f"🥶 {label}: صقيع — الصغرى {ltr(round(lo))}°"))
        if hi is not None and hi >= 32:
            out.append((f"{date}:heat", f"🥵 {label}: حرّ شديد — العظمى {ltr(round(hi))}°"))
    return out


def weather_report():
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat:
        print("weather: no telegram secrets — skipped")
        return
    try:
        now = berlin_now()
        try:
            with open(WEATHER_STATE) as f:
                st = json.load(f)
        except Exception:
            st = {}
        today = now.strftime("%Y-%m-%d")
        sent_alerts = st.get("alerts", [])
        changed = False
        fc = None

        # 1) التقرير اليومي
        if st.get("daily") != today and now.hour >= REPORT_FROM_HOUR:
            fc = fetch_forecast()
            if tg_send(token, chat, build_report(fc, now), parse_mode="HTML"):
                st["daily"] = today
                changed = True
                print("weather: daily report sent")
            time.sleep(1.1)

        # 2) التنبيهات (نهاراً فقط)
        if now.hour in ALERT_HOURS:
            esc = lambda x: html.escape(str(x or ""), quote=False)
            # أ) تحذيرات الخدمة الألمانية للأرصاد (DWD)
            try:
                alerts = [a for a in fetch_dwd_alerts()
                          if str(a.get("severity", "")).lower() in MIN_SEVERITY]
            except Exception as e:
                print("weather: dwd alerts failed:", e, file=sys.stderr)
                alerts = []
            new = [a for a in alerts if f"dwd:{a.get('alert_id') or a.get('id')}" not in sent_alerts]
            if new:
                tr = translate_many([a.get("event_de") or a.get("headline_de") or "" for a in new])
                for a in new:
                    key = f"dwd:{a.get('alert_id') or a.get('id')}"
                    ev = a.get("event_de") or a.get("headline_de") or "تحذير جوي"
                    ev_ar = tr.get(ev.replace("\n", " "), ev)
                    sev = {"moderate": "🟠 تحذير", "severe": "🔴 تحذير شديد", "extreme": "🟣 تحذير بالغ الخطورة"}.get(
                        str(a.get("severity", "")).lower(), "⚠️ تحذير")
                    until = ""
                    try:
                        from zoneinfo import ZoneInfo
                        e = datetime.fromisoformat(a["expires"]).astimezone(ZoneInfo("Europe/Berlin"))
                        until = f"\n⏳ حتى {ltr(e.strftime('%d/%m %H:%M'))}"
                    except Exception:
                        pass
                    if tg_send(token, chat, f"{sev} من خدمة الأرصاد الألمانية (DWD)\n\n<b>{esc(ev_ar)}</b>{until}",
                               parse_mode="HTML"):
                        sent_alerts.append(key)
                        changed = True
                    time.sleep(1.1)
            # ب) تنبيهات من التوقعات
            fc = fc or fetch_forecast()
            for key, text in forecast_alerts(fc):
                if key in sent_alerts:
                    continue
                if tg_send(token, chat, f"⚠️ {text}", parse_mode=None):
                    sent_alerts.append(key)
                    changed = True
                time.sleep(1.1)

        if changed:
            st["alerts"] = sent_alerts[-60:]
            with open(WEATHER_STATE, "w") as f:
                json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        print("weather failed:", e, file=sys.stderr)


NEWS_STAMP = "data/news_last_run.txt"
NEWS_EVERY_H = 4


def news_due():
    try:
        last = float(open(NEWS_STAMP).read().strip())
    except Exception:
        return True
    return time.time() - last >= (NEWS_EVERY_H * 3600 - 20 * 60)


if __name__ == "__main__":
    try:
        if news_due():
            main()
            os.makedirs("data", exist_ok=True)
            open(NEWS_STAMP, "w").write(str(time.time()))
        else:
            print("news: not due yet")
    finally:
        weather_report()
