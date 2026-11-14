import requests, json, re, time, os, random, datetime
from bs4 import BeautifulSoup
from collections import Counter, defaultdict

BASE = "https://nextjoby.com"
LIST_QUERY = "jobs_ppp=384"
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
MODEL = "gpt-4o-mini"
HEADERS = {"User-Agent": "Mozilla/5.0 (NextJobyPostBot)"}
MAX_SCAN = int(os.environ.get("MAX_SCAN", 1500))  # نمسح أولاً أول 1500 وظيفة
MAX_SCAN_HARD = int(os.environ.get("MAX_SCAN_HARD", 8000))  # سقف التوسع إن لم نجد وظائف تقنية كافية
LIST_SORT = os.environ.get("LIST_SORT", "")   # مثال: "orderby=newest" (انظر الشرح) يُضاف إلى رابط القائمة
POSTS_PER_DAY = int(os.environ.get("POSTS_PER_DAY", 15))
FORCE = os.environ.get("FORCE", "").lower() == "true"   # إعادة التوليد الآن
LINK_MODE = os.environ.get("LINK_MODE", "site").lower()  # site = رابط صفحتك | jooble = رابط التقديم المباشر
QUEUE_FILE = "posts.json"

# تصنيفات لا تناسب جمهور LinkedIn الأمريكي
EXCLUDE = ["Emplois", "Offres", "Hôtellerie", "Industrie &", "Informatique",
           "Logistique", "Ressources", "Santé", "BTP", "Commerce &",
           "Communication", "Unknown"]

STYLES = [
 "Start with a question to the reader",
 "Short bullet list with ✅ marks",
 "Two-line mini story, then the role",
 "Lead with a striking fact about the role or location",
 "Challenge the reader: 'Ready for...?'",
 "Career tip first, then the opportunity",
 "Urgent tone: 'New opening today'",
 "Who is this role perfect for?",
 "Start with 'Looking for your next step in {state}?'",
 "Start with a bold one-line promise, then details",
]

STOP = {"remote", "intern", "internship", "the", "and", "for", "with", "our", "new"}
BAD_WORDS = ["panelist", "focus group", "survey", "paid study", "mystery shopper"]

def words(title):
    return {w for w in re.findall(r"[a-z]+", title.lower()) if w not in STOP and len(w) > 2}

def too_similar(title, seen_sets):
    w = words(title)
    return any(w and o and len(w & o) / len(w | o) >= 0.5 for o in seen_sets)

def clean(text):
    """LinkedIn لا يدعم markdown: نزيل النجوم والعناوين"""
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    return text.strip()

# ===== نركّز على IT والهندسة فقط =====
INCLUDE_CI = re.compile(
    r"\b(engineer\w*|software|developer|programmer|devops|cloud|cyber\w*|"
    r"data (?:scientist|analyst|engineer)|machine learning|artificial intelligence|"
    r"network (?:admin\w*|engineer\w*|security)|sysadmin|systems? (?:admin\w*|analyst|engineer\w*)|"
    r"full[- ]?stack|front[- ]?end|back[- ]?end|database|help ?desk|technical support|"
    r"architect|robotics|firmware|embedded|iot|servicenow|salesforce|java|python|kubernetes)\b", re.I)
INCLUDE_CS = re.compile(r"\b(IT|AI|ML|QA|SRE|DBA|SAP)\b")   # حساسة لحالة الأحرف

def is_tech(title):
    return bool(INCLUDE_CI.search(title) or INCLUDE_CS.search(title))

# ===== ممنوعات: عدّل القوائم كما تشاء =====
# (أ) قوية: ترفض الوظيفة إن ظهرت في أي مكان من الوصف
STRONG_BLOCK = [
    "casino", "gambling", "sportsbook", "betting", "lottery", "poker", "slot machines",
    "draftkings", "fanduel", "caesars", "mgm resorts",
    "brewery", "brewing company", "distillery", "winery", "liquor", "alcoholic beverage",
    "tobacco", "cigarette", "cigar", "vaping", "philip morris", "altria",
    "adult entertainment", "adult content", "adult store", "strip club", "nightclub", "escort",
    "onlyfans", "pornography", "cannabis", "marijuana", "dispensary",
    "payday loan", "mortgage lender", "mortgage loans", "consumer lending", "credit union",
]
# (ب) عادية: تُفحص في العنوان وأول 1000 حرف من الوصف (مقدمة الشركة) فقط،
#     حتى لا نرفض وظيفة بسبب "health insurance" أو "student loan" في المزايا
WEAK_BLOCK = [
    "bank", "banks", "banking", "banker", "bankers", "lender", "lending",
    "loan officer", "loan servicing", "personal loans", "auto loans", "business loans", "home loans",
    "mortgage", "credit card", "credit cards", "investment bank", "wealth management",
    "insurance company", "insurance carrier", "insurance agency", "insurance broker", "insurtech",
    "alcohol", "beer", "wine", "spirits", "bartender", "gaming and wagering", "wagering",
    "jpmorgan", "jp morgan", "wells fargo", "bank of america", "citigroup", "citibank",
    "goldman sachs", "morgan stanley", "capital one", "american express", "discover financial",
    "synchrony", "truist", "pnc", "us bancorp", "fifth third", "navy federal",
    "state farm", "geico", "allstate", "metlife", "prudential", "liberty mutual",
]
def _rx(words):
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
STRONG_RX, WEAK_RX = _rx(STRONG_BLOCK), _rx(WEAK_BLOCK)

def blocked(job):
    head = job["title"] + " " + job["desc"][:1000]
    return bool(WEAK_RX.search(head) or STRONG_RX.search(job["title"] + " " + job["desc"]))

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def get(url, tries=3):
    last = ""
    for i in range(tries):
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=60)
            if r.status_code in (403, 429) or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last = str(e)[:150]
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url} -> {last}")

LIST_QUERIES = [LIST_QUERY, "jobs_ppp=96", ""]     # إن فشل الحجم الكبير نجرّب أصغر ثم العادي

def _page_url(page, q):
    base = f"{BASE}/jobs/" if page == 1 else f"{BASE}/jobs/page/{page}/"
    qs = "&".join(x for x in (q, LIST_SORT) if x)
    return base + (f"?{qs}" if qs else "")

def scan_listings(done_urls=frozenset(), pool_target=90):
    # نختار أول صيغة تعمل على الصفحة الأولى
    q, first_html, errors = None, None, []
    for cand in LIST_QUERIES:
        try:
            first_html = get(_page_url(1, cand)); q = cand; break
        except RuntimeError as e:
            errors.append(str(e)); print("listing failed:", e)
    if first_html is None:
        raise RuntimeError("Cannot read the jobs listing (site blocks the bot or is down): " + " | ".join(errors))
    print("listing query in use:", repr(q))
    seen, cards, page, usable = set(), [], 1, 0
    while True:
        if len(cards) >= MAX_SCAN_HARD or (len(cards) >= MAX_SCAN and usable >= pool_target):
            break
        try:
            html = first_html if page == 1 else get(_page_url(page, q))
        except RuntimeError as e:
            print("stop at page", page, e); break
        soup = BeautifulSoup(html, "html.parser")
        found = 0
        for h in soup.select("h2 a[href*='/job/']"):
            link = h["href"]
            if link in seen: continue
            seen.add(link); found += 1
            cat = h.find_next("a", href=re.compile("/job-category/"))
            loc = h.find_next("a", href=re.compile("/job-location/"))
            cards.append({
                "url": link,
                "title": h.get_text(strip=True),
                "category": cat.get_text(strip=True) if cat else "General",
                "location": loc.get_text(strip=True) if loc else "USA",
            })
            c = cards[-1]
            if c["url"] not in done_urls and is_tech(c["title"]) and not any(x in c["category"] for x in EXCLUDE):
                usable += 1
        print(f"page {page}: +{found} (total {len(cards)}, tech not yet posted: {usable})")
        if found == 0: break
        page += 1
        time.sleep(2)
    if not cards:
        raise RuntimeError("Listing page loaded but no job cards were found (site layout changed?)")
    print(f"scan finished: {len(cards)} jobs scanned, {usable} tech jobs not posted yet")
    return cards

def diversify(cards, n, done_urls):
    cards = [c for c in cards
             if c["url"] not in done_urls
             and is_tech(c["title"])
             and not any(x in c["category"] for x in EXCLUDE)]
    print("tech candidates:", len(cards))
    if not cards:
        print("WARNING: no new tech jobs found. Either everything scanned was already posted "
              "(site not sorted by newest / import stalled) or the filters are too strict.")
    # بدون خلط: نحافظ على ترتيب الموقع (الأحدث أولاً)
    by_cat = defaultdict(list)
    for c in cards: by_cat[c["category"]].append(c)
    seen_sets, used_states, out = [], Counter(), []
    cats = list(by_cat)
    max_per_state = max(3, n // 8)
    while len(out) < n and any(by_cat.values()):
        progressed = False
        for cat in cats:
            while by_cat[cat]:
                c = by_cat[cat].pop(0)
                state = c["location"].split(",")[-1].strip()
                if too_similar(c["title"], seen_sets) or used_states[state] >= max_per_state:
                    continue
                seen_sets.append(words(c["title"])); used_states[state] += 1
                out.append(c); progressed = True
                break
            if len(out) >= n: break
        if not progressed: break
    return out

def parse_job(url):
    soup = BeautifulSoup(get(url), "html.parser")
    title = soup.find("h1").get_text(strip=True)
    loc = soup.select_one('a[href*="/job-location/"]')
    cat = soup.select_one('a[href*="/job-category/"]')
    text = soup.get_text("\n", strip=True)
    desc = text.split("Job Description", 1)[-1]
    desc = re.split(r"Verified Remote Jobs|Share this post", desc)[0]
    apply_url = ""
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True).lower() in ("apply now", "apply") and "jooble" in a["href"]:
            apply_url = a["href"]; break
    if not apply_url:                                   # احتياط: أول رابط jooble في الصفحة
        a = soup.find("a", href=re.compile(r"jooble\.org/redirect"))
        apply_url = a["href"] if a else ""
    return {"url": url, "apply_url": apply_url, "title": title,
            "location": loc.get_text(strip=True) if loc else "USA",
            "category": cat.get_text(strip=True) if cat else "General",
            "desc": desc[:4500]}

def is_real(job):
    d = job["desc"].lower()
    t = job["title"].lower()
    bad = ["not an active job", "not an active listing", "specific openings have not"]
    if any(b in d for b in bad) or len(d) < 200:
        return False
    if any(w in t or w in d[:800] for w in BAD_WORDS):      # عروض مشبوهة
        return False
    if blocked(job):                                         # بنوك / خمر / قمار ...
        return False
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", job["title"])]
    if years and max(years) < datetime.date.today().year:    # وظائف سنوات ماضية
        return False
    return True

def pick_diverse(n, done_urls):
    cards = scan_listings(done_urls, pool_target=max(90, n * 6))
    chosen, fps = [], set()
    for c in diversify(cards, n + 60, done_urls):
        try: j = parse_job(c["url"])
        except Exception as e: print("skip", c["url"], e); continue
        if not is_real(j): continue
        fp = re.sub(r"\W+", " ", j["desc"].lower())[:150]
        if fp in fps: continue           # نفس الوصف في مدن مختلفة
        fps.add(fp)
        chosen.append(j)
        print(len(chosen), j["title"], "-", j["location"], "|", j["category"])
        if len(chosen) == n: break
        time.sleep(1)
    return chosen

FIRST_PERSON = re.compile(r"\b(our client|our team|join our|we are|we're|we want|we offer|we need|we'd|our company|with us|join us)\b", re.I)

BULLETS = ["🔹", "▪️", "➡️", "✔️", "🔸"]

PROMPT = """You are a LinkedIn growth writer for a US job board. Write ONE original,
detailed LinkedIn job post for US job seekers, based ONLY on the job below.

Title: {title}
Location: {location}
Category: {category}
Original description (rewrite everything, never copy sentences):
{desc}

FOLLOW THIS STRUCTURE EXACTLY (it is a skeleton, not content to copy):

🚀 [Job Title] | [Company] | [City, ST] 🇺🇸

[Opening: 1-2 sentences. Style for this post: {style}]

[1 sentence about the company/team and what the role is]

📍 [City, State]
💼 [Type: internship / full-time / part-time / remote + season/year if stated]
🎓 [Who it is for / education level]      <- only if stated
💰 [Pay or salary range]                  <- only if stated
⏰ [Hours / schedule]                     <- only if stated

What you'll work on:
{b} [duty 1]
{b} [duty 2]
(5 to 7 bullets, short, rewritten in your own words)

Skills that can help you stand out:
✅ [skill or requirement 1]
✅ [skill or requirement 2]
(4 to 7 bullets)

⚠️ [One short note ONLY if the description contains a real caveat: work authorization,
clearance, travel, shifts, physical requirements. Skip this line otherwise.]

👉 View the full details & apply:
{link}

[8 hashtags on ONE line]

RULES:
- VOICE: write in the THIRD PERSON, as a job board sharing someone else's opening. NEVER say "we", "our", "us", "our client", "our team", "join our team" or "we want to hear from you". Say "[Company] is hiring", "this team is looking for", "the role involves". If the company is unknown, say e.g. "A San Diego IT team is hiring" (never "our client").
- Plain text only. No markdown, no asterisks, no bold, no headings.
- Extract the real company name from the description. If it is not stated, write the title and city only; never invent a company.
- Skip any info line (🎓 💰 ⏰ ⚠️) if that information is not in the description. NEVER invent facts, numbers or requirements.
- Use natural SEO wording: job title, city, state, "hiring", "internship" or "entry-level" when true, "apply".
- Hashtags: exactly 8, short CamelCase, each under 22 characters: role-specific, industry, #USJobs, state (e.g. #MinnesotaJobs), company if short.
- Total length under 2500 characters. Return only the post text."""

def gen(job, i):
    if LINK_MODE == "jooble" and job.get("apply_url"):
        link = job["apply_url"]                         # رابط Jooble المباشر كما هو (فيه تتبع الإحالة)
    else:
        link = job["url"] + "?utm_source=linkedin"      # رابط صفحتك
    state = job["location"].split(",")[-1].strip()
    style = STYLES[i % len(STYLES)].replace("{state}", state)
    bullet = BULLETS[i % len(BULLETS)]
    prompt = PROMPT.format(style=style, link=link, b=bullet,
                           **{k: job[k] for k in ("title", "location", "category", "desc")})
    for attempt in range(3):
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            json={"model": MODEL, "temperature": 0.8,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120)
        if not r.ok:
            print("API error", r.status_code, r.text[:200]); time.sleep(10); continue
        text = clean(r.json()["choices"][0]["message"]["content"])
        if len(text) > 2990:                     # حد LinkedIn 3000 حرف
            print("too long, retrying"); continue
        if FIRST_PERSON.search(text) and attempt < 2:   # صيغة المتكلم ممنوعة
            print("first-person voice, retrying"); continue
        if link.split("?")[0] not in text:       # نضمن وجود رابط التقديم
            text += f"\n\n👉 Apply: {link}"
        return text
    return None

def build_queue(queue, today, pick_fn, gen_fn, n):
    """دفعة جديدة كل يوم. تُحذف القديمة غير المنشورة فقط إذا نجح توليد الجديدة.
       تعيد (الطابور، نجح؟)."""
    if not FORCE and any(p.get("batch") == today for p in queue):
        print("Today's batch already generated, skipping.")
        return queue, True
    posted = [p for p in queue if p["posted"]]
    done_urls = {p["url"] for p in posted}
    print(f"generating {n} fresh posts (keeping {len(posted)} posted for history)")
    try:
        jobs = pick_fn(n, done_urls)
    except Exception as e:                      # الموقع يحجب الروبوت، أو خطأ آخر
        print("ERROR while fetching jobs:", e)
        return queue, False
    fresh = []
    for i, j in enumerate(jobs):
        t = gen_fn(j, i + len(posted))
        if t:
            fresh.append({"url": j["url"], "title": j["title"], "text": t,
                          "location": j.get("location", "USA"),
                          "category": j.get("category", "General"),
                          "posted": False, "batch": today})
        time.sleep(1)
    if not fresh:
        print("ERROR: 0 posts generated (no matching jobs, or the OpenAI API failed). Keeping the old queue.")
        return queue, False
    stale = sum(1 for p in queue if not p["posted"])
    print(f"dropped {stale} stale unposted posts; added {len(fresh)} fresh ones")
    return posted + fresh, True

if __name__ == "__main__":
    queue = json.load(open(QUEUE_FILE, encoding="utf-8")) if os.path.exists(QUEUE_FILE) else []
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    queue, ok = build_queue(queue, today, pick_diverse, gen, POSTS_PER_DAY)
    queue = queue[-600:]
    json.dump(queue, open(QUEUE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Queue size:", len(queue), "| unposted:", sum(1 for p in queue if not p["posted"]))
    if not ok:
        raise SystemExit(1)          # يجعل التشغيل أحمر في GitHub بدل أن يفشل بصمت
