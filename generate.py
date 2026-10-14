import requests, json, re, time, os, random, datetime
from bs4 import BeautifulSoup
from collections import Counter, defaultdict

BASE = "https://nextjoby.com"
LIST_QUERY = "jobs_ppp=384"
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
MODEL = "gpt-4o-mini"
HEADERS = {"User-Agent": "Mozilla/5.0 (NextJobyPostBot)"}
MAX_SCAN = int(os.environ.get("MAX_SCAN", 600))   # أحدث 600 وظيفة
POSTS_PER_DAY = int(os.environ.get("POSTS_PER_DAY", 15))
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

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.text

def scan_listings():
    seen, cards, page = set(), [], 1
    while len(cards) < MAX_SCAN:
        base = f"{BASE}/jobs/" if page == 1 else f"{BASE}/jobs/page/{page}/"
        try:
            soup = BeautifulSoup(get(f"{base}?{LIST_QUERY}"), "html.parser")
        except Exception as e:
            print("stop at page", page, e); break
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
        print(f"page {page}: +{found} (total {len(cards)})")
        if found == 0: break
        page += 1
        time.sleep(2)
    return cards

def diversify(cards, n, done_urls):
    cards = [c for c in cards
             if c["url"] not in done_urls
             and not any(x in c["category"] for x in EXCLUDE)]
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
    return {"url": url, "title": title,
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
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", job["title"])]
    if years and max(years) < datetime.date.today().year:    # وظائف سنوات ماضية
        return False
    return True

def pick_diverse(n, done_urls):
    cards = scan_listings()
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
- Plain text only. No markdown, no asterisks, no bold, no headings.
- Extract the real company name from the description. If it is not stated, write the title and city only; never invent a company.
- Skip any info line (🎓 💰 ⏰ ⚠️) if that information is not in the description. NEVER invent facts, numbers or requirements.
- Use natural SEO wording: job title, city, state, "hiring", "internship" or "entry-level" when true, "apply".
- Hashtags: exactly 8, short CamelCase, each under 22 characters: role-specific, industry, #USJobs, state (e.g. #MinnesotaJobs), company if short.
- Total length under 2500 characters. Return only the post text."""

def gen(job, i):
    link = job["url"] + "?utm_source=linkedin"
    state = job["location"].split(",")[-1].strip()
    style = STYLES[i % len(STYLES)].replace("{state}", state)
    bullet = BULLETS[i % len(BULLETS)]
    prompt = PROMPT.format(style=style, link=link, b=bullet,
                           **{k: job[k] for k in ("title", "location", "category", "desc")})
    for _ in range(3):
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
        if job["url"] not in text:               # نضمن وجود رابط التقديم
            text += f"\n\n👉 Apply: {link}"
        return text
    return None

def build_queue(queue, today, pick_fn, gen_fn, n):
    """دفعة جديدة كل يوم: تُحذف المنشورات القديمة غير المنشورة، وتُولَّد n جديدة."""
    if any(p.get("batch") == today for p in queue):
        print("Today's batch already generated, skipping.")
        return queue
    stale = sum(1 for p in queue if not p["posted"])
    queue = [p for p in queue if p["posted"]]          # نحتفظ بالمنشور فقط (للسجل ومنع التكرار)
    done_urls = {p["url"] for p in queue}
    print(f"dropped {stale} stale unposted posts; generating {n} fresh ones")
    for i, j in enumerate(pick_fn(n, done_urls)):
        t = gen_fn(j, i + len(queue))
        if t:
            queue.append({"url": j["url"], "title": j["title"], "text": t,
                          "posted": False, "batch": today})
        time.sleep(1)
    return queue

if __name__ == "__main__":
    queue = json.load(open(QUEUE_FILE, encoding="utf-8")) if os.path.exists(QUEUE_FILE) else []
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    queue = build_queue(queue, today, pick_diverse, gen, POSTS_PER_DAY)
    queue = queue[-600:]
    json.dump(queue, open(QUEUE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Queue size:", len(queue), "| unposted:", sum(1 for p in queue if not p["posted"]))
