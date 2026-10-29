import json, os, sys, requests
import datetime as dt

TOKEN = os.environ["LI_TOKEN"].strip()
H = {"Authorization": f"Bearer {TOKEN}", "X-Restli-Protocol-Version": "2.0.0"}

from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")                    # يتكيف تلقائياً مع التوقيت الصيفي والشتوي
SLOTS = int(os.environ.get("LI_POSTS_PER_DAY", 24))  # عدد المنشورات يومياً
START_HOUR = 7                                       # 7:00 صباحاً بتوقيت نيويورك
END_HOUR = 22                                        # 10:00 مساءً بتوقيت نيويورك
GRACE_MIN = 30                                       # مهلة للتعويض بعد 10:00 مساءً
MIN_GAP_MIN = 20                                     # أقل فاصل بين منشورين

def parse(s):
    return dt.datetime.fromisoformat(s)

def should_post(now, posts, manual=False):
    """يوزّع المنشورات بالتساوي بين 7:00 و22:00 بتوقيت نيويورك، ويعوّض ما فاته."""
    if manual:
        return True, "manual run"
    now_et = now.astimezone(TZ)
    start = now_et.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    end = now_et.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
    if now_et < start or now_et > end + dt.timedelta(minutes=GRACE_MIN):
        return False, f"outside US posting hours ({START_HOUR}:00-{END_HOUR}:00 ET)"
    span_min = (end - start).total_seconds() / 60
    step = span_min / (SLOTS - 1) if SLOTS > 1 else span_min
    elapsed_min = (now_et - start).total_seconds() / 60
    due = min(SLOTS, int(elapsed_min / step) + 1)
    times = [parse(p["posted_at"]) for p in posts if p.get("posted_at")]
    today = [t for t in times if t >= start]
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
