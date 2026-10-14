import json, os, sys, requests
import datetime as dt

TOKEN = os.environ["LI_TOKEN"].strip()
H = {"Authorization": f"Bearer {TOKEN}", "X-Restli-Protocol-Version": "2.0.0"}

SLOTS = 15            # عدد المنشورات يومياً
START_H = 12          # بداية النافذة: 12:00 UTC = 8:00 صباحاً نيويورك (EDT)
WINDOW_H = 16         # النافذة تنتهي 04:00 UTC
MIN_GAP_MIN = 20      # أقل فاصل بين منشورين

def parse(s):
    return dt.datetime.fromisoformat(s)

def should_post(now, posts, manual=False):
    """هل يجب النشر الآن؟ يعوّض المنشورات الفائتة إذا تأخر GitHub."""
    if manual:
        return True, "manual run"
    ws = now.replace(hour=START_H, minute=0, second=0, microsecond=0)
    if now < ws:
        ws -= dt.timedelta(days=1)
    elapsed_h = (now - ws).total_seconds() / 3600
    if elapsed_h >= WINDOW_H:
        return False, "outside posting window"
    due = min(SLOTS, int(elapsed_h) + 1)
    times = [parse(p["posted_at"]) for p in posts if p.get("posted_at")]
    today = [t for t in times if t >= ws]
    if len(today) >= due:
        return False, f"up to date ({len(today)}/{due})"
    if today and (now - max(today)).total_seconds() < MIN_GAP_MIN * 60:
        return False, "too soon after last post"
    return True, f"behind schedule ({len(today)}/{due})"

def person_urn():
    pid = os.environ.get("LI_PERSON_ID", "").strip()
    if not pid:
        r = requests.get("https://api.linkedin.com/v2/userinfo", headers=H, timeout=30)
        if not r.ok:
            sys.exit(f"Cannot read profile ({r.status_code}): {r.text}")
        pid = r.json()["sub"]
    return pid if pid.startswith("urn:li:") else f"urn:li:person:{pid}"

def org_urn():
    oid = os.environ.get("LI_ORG_ID", "").strip()
    if not oid:
        return None
    return oid if oid.startswith("urn:li:") else f"urn:li:organization:{oid}"

def publish(author, text):
    body = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "NONE"}},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    r = requests.post("https://api.linkedin.com/v2/ugcPosts", json=body, headers=H, timeout=60)
    print(author, r.status_code, r.text[:300])
    if r.status_code == 401:
        print("TOKEN EXPIRED: generate a new LinkedIn token and update the LI_TOKEN secret.")
    return r.ok

if __name__ == "__main__":
    posts = json.load(open("posts.json", encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    go, why = should_post(now, posts, manual)
    print(now.isoformat(), "->", "POST" if go else "SKIP", "|", why)
    if not go:
        sys.exit(0)

    nxt = next((p for p in posts if not p["posted"]), None)
    if not nxt:
        print("Queue empty, nothing to post."); sys.exit(0)

    nxt["text"] = nxt["text"].replace("**", "").replace("__", "")
    ok_personal = publish(person_urn(), nxt["text"])

    org = org_urn()
    if org and not publish(org, nxt["text"]):
        print("WARNING: company page post failed (check w_organization_social permission)")

    if ok_personal:
        nxt["posted"] = True
        nxt["posted_at"] = now.isoformat()
        json.dump(posts, open("posts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    else:
        sys.exit(1)
