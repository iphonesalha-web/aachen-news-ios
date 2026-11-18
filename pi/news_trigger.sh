#!/bin/bash
# يثبّت مؤقتاً على الـ Pi يشغّل workflow الأخبار على GitHub كل ساعة (لأن جدولة GitHub تتأخر لساعات).
# التوكن يُدخل هنا مباشرة على الـ Pi ولا يُخزَّن في أي مستودع.
set -e
[ "$(id -u)" = 0 ] || { echo "شغّله بـ sudo"; exit 1; }
REPO="iphonesalha-web/aachen-news-ios"
ENVF=/etc/aachen-news-trigger.env
if [ ! -s "$ENVF" ]; then
  read -r -s -p "الصق توكن GitHub (لن يظهر أثناء اللصق): " TK < /dev/tty; echo
  [ -n "$TK" ] || { echo "التوكن فارغ"; exit 1; }
  printf 'GH_TOKEN=%s\n' "$TK" > "$ENVF"
  chmod 600 "$ENVF"
fi
cat > /usr/local/bin/aachen-news-trigger <<'SH'
#!/bin/bash
. /etc/aachen-news-trigger.env
code=$(curl -s -o /tmp/aachen-trigger.out -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/iphonesalha-web/aachen-news-ios/actions/workflows/news.yml/dispatches \
  -d '{"ref":"main"}')
echo "$(date '+%F %T') HTTP $code"
[ "$code" = 204 ]
SH
chmod +x /usr/local/bin/aachen-news-trigger
cat > /etc/systemd/system/aachen-news-trigger.service <<'SH'
[Unit]
Description=Trigger Aachen news workflow
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/aachen-news-trigger
SH
cat > /etc/systemd/system/aachen-news-trigger.timer <<'SH'
[Unit]
Description=Hourly Aachen news trigger
[Timer]
OnCalendar=*:07
Persistent=true
[Install]
WantedBy=timers.target
SH
systemctl daemon-reload
systemctl enable --now aachen-news-trigger.timer >/dev/null
echo "تجربة الآن..."
/usr/local/bin/aachen-news-trigger && echo "✅ تم: سيعمل كل ساعة عند الدقيقة 7" || echo "❌ فشل — تحقق من التوكن وصلاحية Actions"
