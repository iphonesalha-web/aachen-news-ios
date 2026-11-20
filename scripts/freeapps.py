#!/usr/bin/env python3
"""تنبيه تطبيقات iPhone التي أصبحت مجانية لفترة محدودة (تطبيقات فقط، بدون ألعاب)."""
import os, re, json, html, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT = os.environ.get("TG_CHAT_ID", "")
STATE = "data/freeapps_state.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
FEEDS = [
    "https://www.mydealz.de/rss/gruppe/apps",
    "https://www.iphone-ticker.de/feed/",
]
FREE_RE = re.compile(r"(kostenlos|gratis|free|0\s?(€|eur)|0,00|statt|zeitweise)", re.I)
IOS_RE = re.compile(r"(ios|iphone|ipad|app store|apple)", re.I)
ANDROID_RE = re.compile(r"(google play|playstore|android only|wearos|watchface)", re.I)
APP_RE = re.compile(r"apps\.apple\.com/[^\s\"'<>]*?/id(\d{6,})")

def get(url, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(r, timeout=25).read().decode("utf8", "ignore")
        except Exception:
            time.sleep(2 * (i + 1))
    return ""

def lookup(aid):
    for cc in ("de", "us"):
        t = get(f"https://itunes.apple.com/lookup?id={aid}&country={cc}", 2)
        try:
            res = json.loads(t).get("results", [])
        except Exception:
            res = []
        if res:
            return res[0], cc
    return None, None

def ltr(s):
    return "⁦" + s + "⁩"

def tg(text):
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text, "disable_web_page_preview": "false"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data), timeout=25).read()
        return True
    except Exception as e:
        print("telegram error", e)
        return False

def candidates():
    out = {}
    for f in FEEDS:
        txt = get(f)
        if not txt:
            print("feed failed", f); continue
        try:
            items = ET.fromstring(txt).iter("item")
        except Exception:
            print("parse failed", f); continue
        for it in items:
            title = html.unescape(it.findtext("title") or "")
            blob = html.unescape(ET.tostring(it, encoding="unicode"))
            if ANDROID_RE.search(title) and not re.search(r"ios|iphone", title, re.I):
                continue
            if not (FREE_RE.search(title) and IOS_RE.search(title + blob)):
                continue
            m = APP_RE.search(blob)
            if m:
                out.setdefault(m.group(1), title)
    return out

def main():
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    sent = 0
    for aid, title in candidates().items():
        if aid in state and time.time() - state[aid] < 14 * 86400:
            continue
        a, cc = lookup(aid)
        if not a or a.get("price", 1) != 0:
            continue
        devs = " ".join(a.get("supportedDevices", [])).lower()
        if a.get("supportedDevices") and "iphone" not in devs:
            continue
        genres = [g.lower() for g in a.get("genres", [])]
        if "games" in genres:
            continue
        if not a.get("trackName") or a.get("kind") != "software":
            continue
        # تجاهل التطبيقات المجانية دائماً مع مشتريات داخلية: العنوان يجب أن يوحي بالعرض
        if not re.search(r"(statt|kostenlos|gratis|free|zeitweise|0\s?€)", title, re.I):
            continue
        name = a["trackName"]
        link = a.get("trackViewUrl", "").split("?")[0]
        msg = ("🆓 تطبيق آيفون أصبح مجانياً لفترة محدودة\n\n"
               f"📱 {name}\n"
               f"🏷️ السعر الآن: {ltr('0')}\n"
               f"🗂️ الفئة: {a.get('primaryGenreName','')}\n"
               f"📝 {title}\n\n"
               f"🔗 {link}\n\n⚠️ تأكد من السعر في المتجر قبل التحميل، فالعرض قد ينتهي في أي لحظة.")
        if tg(msg):
            state[aid] = time.time(); sent += 1
        if sent >= 5:
            break
    os.makedirs("data", exist_ok=True)
    json.dump(state, open(STATE, "w"))
    print("sent", sent)

if __name__ == "__main__":
    main()
