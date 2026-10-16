"""يرسل بيناً واحداً إلى Make (Webhook) كلما حان موعده، ويعوّض الفائت."""
import json, os, sys, requests
import datetime as dt

QUEUE = "pins/queue.json"
HOOK = os.environ.get("MAKE_WEBHOOK_URL", "").strip()
SLOTS = int(os.environ.get("PIN_SLOTS", 10))   # عدد البيبنات يومياً
START_H = int(os.environ.get("PIN_START_H", 12))     # بداية اليوم (UTC)
SPAN_H = int(os.environ.get("PIN_SPAN_H", 14))       # البيبنات تتوزع على هذه الساعات
WINDOW_H = int(os.environ.get("PIN_WINDOW_H", 16))   # بعدها يتوقف حتى اليوم التالي
MIN_GAP_MIN = 20

def should_post(now, items):
    ws = now.replace(hour=START_H, minute=0, second=0, microsecond=0)
    if now < ws:
        ws -= dt.timedelta(days=1)
    elapsed = (now - ws).total_seconds() / 3600
    if elapsed >= WINDOW_H:
        return False, "outside posting window"
    step = SPAN_H / (SLOTS - 1) if SLOTS > 1 else SPAN_H
    due = min(SLOTS, int(elapsed / step) + 1)
    times = [dt.datetime.fromisoformat(i["posted_at"]) for i in items if i.get("posted_at")]
    today = [t for t in times if t >= ws]
    if len(today) >= due:
        return False, f"up to date ({len(today)}/{due})"
    if today and (now - max(today)).total_seconds() < MIN_GAP_MIN * 60:
        return False, "too soon after last pin"
    return True, f"behind schedule ({len(today)}/{due})"

if __name__ == "__main__":
    test = "--test" in sys.argv
    if not HOOK:
        print("MAKE_WEBHOOK_URL not set, skipping Pinterest."); sys.exit(0)
    items = json.load(open(QUEUE, encoding="utf-8")) if os.path.exists(QUEUE) else []
    now = dt.datetime.now(dt.timezone.utc)
    go, why = (True, "test run") if test else should_post(now, items)
    print(now.isoformat(), "->", "PIN" if go else "SKIP", "|", why)
    if not go:
        sys.exit(0)
    nxt = next((i for i in items if not i["posted"]), None)
    if not nxt:
        print("Pin queue empty."); sys.exit(0)

    payload = {k: nxt[k] for k in ("title", "description", "link", "image_url", "keywords")}
    r = requests.post(HOOK, json=payload, timeout=30)
    print("Make webhook:", r.status_code, r.text[:200])
    if r.ok and not test:
        nxt["posted"] = True
        nxt["posted_at"] = now.isoformat()
        json.dump(items, open(QUEUE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    elif not r.ok:
        sys.exit(1)
