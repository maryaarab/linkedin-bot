"""يولّد صور Pinterest + ملف CSV جاهزاً للرفع (Bulk create Pins) من منشورات اليوم."""
import csv, datetime as dt, hashlib, json, os, re, glob, requests
from PIL import Image, ImageDraw, ImageFont

REPO = os.environ.get("GITHUB_REPOSITORY", "maryaarab/linkedin-bot")
BOARD = os.environ.get("PIN_BOARD", "US Jobs")          # اسم اللوحة كما في Pinterest
START_H = int(os.environ.get("PIN_START_H", 13))         # أول نشر 13:00 UTC
STEP_MIN = int(os.environ.get("PIN_STEP_MIN", 60))       # فاصل بين البيبنات
KEEP_DAYS = 14
OUT = "pins"
IMG_DIR = f"{OUT}/images"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/{IMG_DIR}"
MODEL = "gpt-4o-mini"

PALETTES = [  # (لون أعلى، لون أسفل، لون التمييز)
    ("#0B1F3A", "#1F5FA8", "#FFC845"), ("#12372A", "#2E8B6A", "#FFD166"),
    ("#2A0A4A", "#6A35B5", "#FFB347"), ("#3B0D1E", "#B02E56", "#FFE08A"),
    ("#06283D", "#0E7C9B", "#7CF2D4"), ("#1C1F3A", "#4B5BD7", "#FF9F68"),
    ("#2B2D42", "#5C6B8A", "#F4D35E"), ("#0F3D3E", "#1E8E85", "#FFC857"),
]
FONT_BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
             "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]
FONT_REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"]
W, H = 1000, 1500
USE_GPT = os.environ.get("PIN_USE_GPT", "").lower() == "true"   # الافتراضي: بدون API
PIN_LIMIT = int(os.environ.get("PIN_SLOTS", 10))                # نصنع صور البيبنات التي ستُنشر فقط

def font(size, bold=True):
    for p in (FONT_BOLD if bold else FONT_REG):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def hex2rgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= max_w:
            cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def short_title(t):
    t = re.split(r"\s[–—-]\s", t)[0]
    return t if len(t) <= 80 else t[:77].rsplit(" ", 1)[0] + "..."

# ---------- رسومات المجال (بالمكتبة نفسها، بلا API) ----------
def motif_kind(title):
    t = short_title(title).lower()
    if re.search(r"cyber|security|infosec", t): return "shield"
    if re.search(r"mechanical|civil|electrical|manufactur|industrial|aerospace|robot|hardware|firmware|embedded|chemical", t): return "gear"
    if re.search(r"data|analy|machine learning|\bai\b|\bml\b|artificial", t): return "chart"
    if re.search(r"cloud|devops|\bsre\b|network|infrastructure|sysadmin", t): return "cloud"
    return "code"

def draw_motif(img, kind, cx, cy, accent):
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    c = (255, 255, 255, 46)
    ca = accent + (70,)
    if kind == "code":
        d.text((cx, cy), "</>", font=font(330), fill=c, anchor="mm")
    elif kind == "chart":
        base = cy + 170
        for i, h in enumerate([120, 210, 160, 300, 380]):
            x = cx - 230 + i * 100
            d.rounded_rectangle([x, base - h, x + 70, base], radius=14, fill=ca if i == 4 else c)
        d.line([cx - 260, base + 14, cx + 270, base + 14], fill=c, width=8)
    elif kind == "shield":
        pts = [(cx - 190, cy - 210), (cx, cy - 280), (cx + 190, cy - 210), (cx + 190, cy + 20), (cx, cy + 270), (cx - 190, cy + 20)]
        d.polygon(pts, fill=c)
        d.line([(cx - 85, cy + 10), (cx - 20, cy + 85), (cx + 100, cy - 70)], fill=ca, width=34, joint="curve")
    elif kind == "cloud":
        d.ellipse([cx - 260, cy - 30, cx - 20, cy + 190], fill=c)
        d.ellipse([cx - 140, cy - 160, cx + 120, cy + 100], fill=c)
        d.ellipse([cx + 20, cy - 70, cx + 270, cy + 190], fill=c)
        d.rounded_rectangle([cx - 200, cy + 60, cx + 210, cy + 190], radius=60, fill=c)
        d.rounded_rectangle([cx - 80, cy + 230, cx + 80, cy + 262], radius=12, fill=ca)
    elif kind == "gear":
        import math
        R, r, teeth = 230, 175, 10
        pts = []
        for i in range(teeth * 2):
            ang = math.pi * i / teeth
            rad = R if i % 2 == 0 else r
            for off in (-0.07, 0.07):
                a = ang + off * (1 if i % 2 == 0 else -1)
                pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, fill=c)
        d.ellipse([cx - 80, cy - 80, cx + 80, cy + 80], fill=ca)
    img.alpha_composite(ov) if img.mode == "RGBA" else img.paste(ov, (0, 0), ov)

def pill(d, x, y, text, fnt, fill, fg, pad=30, h=70):
    w = int(d.textlength(text, font=fnt)) + pad * 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=fill)
    d.text((x + pad, y + h // 2), text, font=fnt, fill=fg, anchor="lm")
    return x + w

def make_image(title, location, category, path, company="", badge=""):
    top, bottom, accent = PALETTES[int(hashlib.md5(title.encode()).hexdigest(), 16) % len(PALETTES)]
    t1, b1, acc = hex2rgb(top), hex2rgb(bottom), hex2rgb(accent)
    img = Image.new("RGB", (W, H), t1)
    g = ImageDraw.Draw(img)
    for y in range(H):                                   # تدرج لوني
        k = y / (H - 1)
        g.line([(0, y), (W, y)], fill=tuple(int(t1[i] + (b1[i] - t1[i]) * k) for i in range(3)))
    img = img.convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)                              # دوائر زخرفية
    od.ellipse([-220, H - 520, 420, H + 120], fill=(255, 255, 255, 22))
    od.ellipse([W - 330, -160, W + 230, 400], fill=acc + (38,))
    od.ellipse([W - 220, H - 700, W + 260, H - 220], fill=(255, 255, 255, 14))
    img.alpha_composite(ov)
    draw_motif(img, motif_kind(title), 735, 330, acc)
    d = ImageDraw.Draw(img)

    # الترويسة
    d.text((70, 90), "NEXTJOBY", font=font(40), fill=(255, 255, 255, 235))
    d.rectangle([70, 148, 190, 156], fill=acc)
    x = pill(d, 70, 215, "NOW HIRING", font(40), acc, hex2rgb(top))
    if badge:
        pill(d, x + 18, 215, badge, font(36), (255, 255, 255), hex2rgb(top), pad=26)

    # كتلة النص: نختار أكبر حجم يتسع
    t = short_title(title)
    loc = location if len(location) <= 30 else location[:27] + "..."
    comp = company if len(company) <= 30 else company[:27] + "..."
    top_y, bottom_limit = 420, H - 400
    for size in range(120, 50, -6):
        f = font(size)
        lines = wrap(d, t, f, W - 150)
        extra = 70 + (80 if comp else 0) + 90
        if (len(lines) <= 5 and all(d.textlength(l, font=f) <= W - 150 for l in lines)
                and top_y + len(lines) * int(size * 1.2) + extra <= bottom_limit):
            break
    y = top_y
    for l in lines:
        d.text((70, y), l, font=f, fill=(255, 255, 255))
        y += int(size * 1.2)
    d.rectangle([70, y + 20, 190, y + 28], fill=acc)
    y += 60
    if comp:
        d.text((70, y), comp, font=font(50), fill=acc)
        y += 80
    # أيقونة الموقع (دبوس مرسوم)
    px, py = 92, y + 36
    d.ellipse([px - 22, py - 22, px + 22, py + 22], fill=acc)
    d.polygon([(px - 17, py + 12), (px + 17, py + 12), (px, py + 52)], fill=acc)
    d.ellipse([px - 8, py - 8, px + 8, py + 8], fill=hex2rgb(top))
    d.text((140, y + 36), loc, font=font(52, bold=False), fill=(255, 255, 255), anchor="lm")

    # زر التقديم + سهم
    d.rounded_rectangle([70, H - 330, W - 70, H - 190], radius=70, fill=acc)
    d.text((W // 2 - 40, H - 260), "APPLY NOW", font=font(64), fill=hex2rgb(top), anchor="mm")
    ax = W - 215
    d.line([(ax - 50, H - 260), (ax + 30, H - 260)], fill=hex2rgb(top), width=14)
    d.polygon([(ax + 10, H - 295), (ax + 60, H - 260), (ax + 10, H - 225)], fill=hex2rgb(top))
    d.text((W // 2, H - 110), "nextjoby.com", font=font(44, bold=False), fill=(255, 255, 255), anchor="mm")
    img.convert("RGB").save(path, optimize=True)

# ---------- العنوان والوصف: من نص المنشور نفسه (بلا API) ----------
EMOJI_RX = re.compile(r"[^\w\s.,;:'\"()&/+#%$\-–—!?@|]")

def clean_txt(s):
    return re.sub(r"\s+", " ", EMOJI_RX.sub("", s)).strip()

def bullets_after(text, header):
    out, on = [], False
    for line in text.splitlines():
        if on:
            if not line.strip():
                if out: break
                continue
            out.append(clean_txt(line))
        elif line.strip().lower().startswith(header.lower()):
            on = True
    return [b for b in out if b]

def parse_post(text):
    lines = text.splitlines()
    parts = [clean_txt(x) for x in (lines[0] if lines else "").split("|")]
    company = parts[1] if len(parts) >= 3 else ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    intro = clean_txt(paras[2]) if len(paras) > 2 else ""
    jt = re.search(r"💼\s*([^\n]+)", text)
    jobtype = clean_txt(jt.group(1)) if jt else ""
    return {"company": company, "intro": intro, "jobtype": jobtype,
            "duties": bullets_after(text, "What you'll work on"),
            "skills": bullets_after(text, "Skills that can help")}

def cut(s, n):
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(",;:") + "..."

def badge_for(title, jobtype):
    t = (title + " " + jobtype).lower()
    if "intern" in t: return "INTERNSHIP"
    if "remote" in t: return "REMOTE"
    if "part" in t: return "PART-TIME"
    if "full" in t: return "FULL-TIME"
    return ""

def template_meta(post, info):
    st = short_title(post["title"])
    title = (f"{st} at {info['company']} | {post['location']}" if info["company"]
             else f"{st} | {post['location']} | Hiring Now")
    desc = f"{st} job in {post['location']}. "
    if info["intro"]:
        desc += cut(re.split(r"(?<=[.!?])\s", info["intro"])[0], 170) + " "
    if info["duties"]:
        desc += "Key duties: " + "; ".join(cut(x, 70) for x in info["duties"][:3]) + ". "
    if info["skills"]:
        desc += "Skills: " + ", ".join(cut(x, 40) for x in info["skills"][:4]) + ". "
    desc += "Hiring now - apply on NextJoby."
    kws = [st, f"{post['category']} jobs", f"jobs in {post['location']}", "US jobs", "hiring now", "tech jobs"]
    return {"title": cut(title, 100), "description": cut(desc, 490), "keywords": kws}

def gpt_meta(post, fallback):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return fallback
    prompt = (
        "Create Pinterest pin metadata for this US job. Pinterest is a search engine.\n"
        f"Job title: {post['title']}\nLocation: {post['location']}\nSource text (only source of truth):\n{post['text'][:2000]}\n\n"
        'Return JSON only: {"title": "...", "description": "...", "keywords": ["..."]}\n'
        "title max 90 chars; description 300-450 chars, plain text, third person, role, city/state, 2-3 real duties/skills, "
        "words 'hiring' and 'apply', end with 'Apply on NextJoby.'; no hashtags, emojis or invented facts; 6 keywords."
    )
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"}, timeout=120,
            json={"model": MODEL, "temperature": 0.6, "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        m = json.loads(r.json()["choices"][0]["message"]["content"])
        if m.get("title") and m.get("description"):
            m["keywords"] = m.get("keywords") or fallback["keywords"]
            return m
    except Exception as e:
        print("GPT meta error, using template:", e)
    return fallback

def ask_meta(post, info):
    base = template_meta(post, info)
    return gpt_meta(post, base) if USE_GPT else base

def prune():
    cutoff = dt.datetime.now().timestamp() - KEEP_DAYS * 86400
    for p in glob.glob(f"{IMG_DIR}/*.png") + glob.glob(f"{OUT}/pins-*.csv"):
        if os.path.getmtime(p) < cutoff:
            os.remove(p)

if __name__ == "__main__":
    os.makedirs(IMG_DIR, exist_ok=True)
    posts = json.load(open("posts.json", encoding="utf-8"))
    today = dt.datetime.now(dt.timezone.utc).date()
    QUEUE_FILE = f"{OUT}/queue.json"
    old_q = json.load(open(QUEUE_FILE, encoding="utf-8")) if os.path.exists(QUEUE_FILE) else []
    if os.environ.get("FORCE", "").lower() != "true" and any(q.get("batch") == today.isoformat() for q in old_q):
        print("Today's pins already generated, skipping."); raise SystemExit(0)
    batch = [p for p in posts if p.get("batch") == today.isoformat() and p.get("location")][:PIN_LIMIT]
    print("pins for", today, ":", len(batch))
    if not batch:
        raise SystemExit(0)

    start = dt.datetime.combine(today, dt.time(START_H, 0))
    rows = []
    for i, p in enumerate(batch):
        name = hashlib.md5(p["url"].encode()).hexdigest()[:12] + ".png"
        info = parse_post(p["text"])
        make_image(p["title"], p["location"], p["category"], f"{IMG_DIR}/{name}",
                   company=info["company"], badge=badge_for(p["title"], info["jobtype"]))
        m = ask_meta(p, info)
        when = start + dt.timedelta(minutes=STEP_MIN * i)
        rows.append({
            "Title": m["title"][:100],
            "Media URL": f"{RAW}/{name}",
            "Pinterest board": BOARD,
            "Thumbnail": "",
            "Description": m["description"][:500],
            "Link": p["url"] + "?utm_source=pinterest&utm_medium=social",
            "Publish date": when.strftime("%Y-%m-%dT%H:%M:%S"),
            "Keywords": ",".join(m["keywords"][:8]),
        })
    # طابور النشر عبر Make: نحتفظ بالمنشور (سجل) ونستبدل غير المنشور بدفعة اليوم
    history = [q for q in old_q if q.get("posted")][-200:]
    fresh = [{"title": r["Title"], "description": r["Description"], "link": r["Link"],
              "image_url": r["Media URL"], "keywords": r["Keywords"],
              "posted": False, "batch": today.isoformat()} for r in rows]
    json.dump(history + fresh, open(QUEUE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    cols = list(rows[0].keys())
    for path in (f"{OUT}/pins-{today.isoformat()}.csv", f"{OUT}/latest.csv"):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader(); w.writerows(rows)
    prune()
    print("wrote", len(rows), "pins ->", f"{OUT}/latest.csv")
