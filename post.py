import json, os, sys, requests

TOKEN = os.environ["LI_TOKEN"].strip()
H = {"Authorization": f"Bearer {TOKEN}", "X-Restli-Protocol-Version": "2.0.0"}

def person_urn():
    pid = os.environ.get("LI_PERSON_ID", "").strip()
    if not pid:   # نجلبه تلقائياً من التوكن
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

posts = json.load(open("posts.json", encoding="utf-8"))
nxt = next((p for p in posts if not p["posted"]), None)
if not nxt:
    print("Queue empty, nothing to post."); sys.exit(0)

nxt["text"] = nxt["text"].replace("**", "").replace("__", "")
ok_personal = publish(person_urn(), nxt["text"])

org = org_urn()
if org:
    # فشل صفحة الشركة لا يمنع الحساب الشخصي
    if not publish(org, nxt["text"]):
        print("WARNING: company page post failed (check w_organization_social permission)")

if ok_personal:
    nxt["posted"] = True
    json.dump(posts, open("posts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
else:
    sys.exit(1)
