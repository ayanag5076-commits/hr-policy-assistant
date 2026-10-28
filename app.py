"""HR Policy Assistant: answers employee questions from the handbook,
cites the section, and hands sensitive topics to a real HR person.
Channels: web chat (/), SMS via Twilio (/sms). HR dashboard: /hr
"""
import os, re, math, json, sqlite3, datetime
from collections import Counter
from xml.sax.saxutils import escape
import requests
from flask import Flask, request, jsonify, render_template, Response

BASE = os.path.dirname(os.path.abspath(__file__))
HANDBOOK = os.environ.get("HANDBOOK_PATH", os.path.join(BASE, "handbook.md"))
DB = os.environ.get("DB_PATH", os.path.join(BASE, "log.db"))
COMPANY = os.environ.get("COMPANY_NAME", "Lone Star Logistics")
BOOKING_URL = os.environ.get("HR_BOOKING_URL", "https://calendar.app.google/your-hr-booking-page")
HR_ALERT_WEBHOOK = os.environ.get("HR_ALERT_WEBHOOK", "")  # optional Slack/Teams incoming webhook
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

EMPLOYEES_PATH = os.environ.get("EMPLOYEES_PATH", os.path.join(BASE, "employees.json"))

app = Flask(__name__, template_folder=BASE)

# ---------- handbook loading and search ----------
STOP = set("a an the and or of to in on for is are be i my me do does can how what when where who much many it at with your you by as if from this that will have has there any we our about get got just need want work".split())

def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]

def load_sections(path):
    sections, title, body = [], None, []
    for line in open(path, encoding="utf-8"):
        if line.startswith("## "):
            if title: sections.append({"title": title, "text": " ".join(body).strip()})
            title, body = line[3:].strip(), []
        elif title and line.strip():
            body.append(line.strip())
    if title: sections.append({"title": title, "text": " ".join(body).strip()})
    return sections

SECTIONS = load_sections(HANDBOOK)
SYN = {"vacation": "pto", "time off": "pto", "days off": "pto", "insurance": "medical",
       "health": "medical", "retirement": "401", "401k": "401", "paid": "pay", "paycheck": "pay",
       "sick": "sick", "funeral": "bereavement", "died": "bereavement", "passed": "bereavement",
       "work from home": "remote", "wfh": "remote", "boots": "boot", "glasses": "vision",
       "teeth": "dental", "dentist": "dental", "counseling": "eap", "therapy": "eap",
       "enroll": "enrollment", "hsa": "hdhp health savings", "fsa": "medical", "baby": "qualifying life event", "married": "qualifying life event"}

def expand(q):
    ql = q.lower()
    return ql + " " + " ".join(v for k, v in SYN.items() if k in ql)

DOCS = [Counter(tokens(s["title"] + " " + s["title"] + " " + s["text"])) for s in SECTIONS]
AVG = sum(sum(d.values()) for d in DOCS) / len(DOCS)
DF = Counter(t for d in DOCS for t in d)

def search(q, k=3):
    qt = tokens(expand(q)); N = len(DOCS); scores = []
    for i, d in enumerate(DOCS):
        L = sum(d.values()); s = 0.0
        for t in qt:
            if t in d:
                idf = math.log(1 + (N - DF[t] + .5) / (DF[t] + .5))
                s += idf * d[t] * 2.2 / (d[t] + 1.2 * (.25 + .75 * L / AVG))
        scores.append((s, i))
    scores.sort(reverse=True)
    return [(s, SECTIONS[i]) for s, i in scores[:k] if s > 0]

# ---------- sensitive topics go to a human ----------
SENSITIVE = {
    "harassment or discrimination": r"harass|discriminat|hostile|inappropriate|touch(ed|ing)|sexual|racis|retaliat|bully|report (my|a) (manager|supervisor|coworker)",
    "safety": r"injur|hurt|unsafe|accident|threat|violence|weapon",
    "medical leave or accommodation": r"fmla|leave of absence|medical leave|maternity|paternity|accommodat|disabilit|pregnan",
    "pay dispute": r"(missing|wrong|short|incorrect).{0,20}(pay|check|paycheck|wage)|(pay|check|paycheck|wage)s?.{0,20}(missing|wrong|short|incorrect)|not paid|unpaid|didn.t get paid",
    "personal crisis": r"suicid|kill myself|self.harm|abuse",
}
URGENT = {"harassment or discrimination", "safety", "personal crisis"}

def classify(q):
    for topic, pat in SENSITIVE.items():
        if re.search(pat, q.lower()): return topic
    return None

# ---------- logging ----------
def db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists log(id integer primary key, ts text, channel text, sender text, question text, answer text, section text, status text)")
    return c

def log(channel, sender, q, a, section, status):
    with db() as c:
        c.execute("insert into log(ts,channel,sender,question,answer,section,status) values(?,?,?,?,?,?,?)",
                  (datetime.datetime.now().isoformat(timespec="seconds"), channel, sender, q, a, section, status))

def alert_hr(topic, q, sender, channel):
    if HR_ALERT_WEBHOOK:
        try:
            requests.post(HR_ALERT_WEBHOOK, json={"text": f":rotating_light: HR handoff ({topic}) via {channel} from {sender}: {q}"}, timeout=5)
        except Exception:
            pass

# ---------- demo employee balances (synthetic data only) ----------
def load_employees():
    try:
        return json.load(open(EMPLOYEES_PATH, encoding="utf-8"))
    except Exception:
        return {"employees": []}

EMP = load_employees()
EMPLOYEES = {e["id"]: e for e in EMP.get("employees", [])}
CARRYOVER_MAX = 40.0

def tenure_years(e):
    hired = datetime.date.fromisoformat(e["hire_date"])
    asof = datetime.date.fromisoformat(EMP.get("as_of", datetime.date.today().isoformat()))
    return (asof - hired).days / 365.25

def accrual_rate(e):
    y = tenure_years(e)
    return 4.62 if y < 3 else 6.15 if y < 6 else 7.69

def fmt_h(h):
    return f"{h:g} hours ({h/8:g} days)" if h % 4 == 0 else f"{h:g} hours (about {h/8:.1f} days)"

def year_end_pto(e):
    """PTO projected on Dec 31 if no more is used: current balance plus remaining 2026 accruals."""
    nxt = datetime.date.fromisoformat(EMP.get("next_accrual", "2026-11-06"))
    periods, end = 0, datetime.date(nxt.year, 12, 31)
    while nxt <= end:
        periods += 1; nxt += datetime.timedelta(days=14)
    return round(e["pto_hours"] + periods * accrual_rate(e), 2)

BAL_RE = r"\b(balance|left|remaining|available|banked|accrued|have)\b"
TIME_RE = r"\b(pto|vacation|sick|time off|days off|hours off|leave)\b"
REQ_RE = r"(take|request|use|get|want|need|have)\b.{0,25}?(\d+(?:\.\d+)?|a|one|two|three|four|five|half a)\s*(day|days|hour|hours|week|weeks)\b.{0,15}\b(off|pto|vacation)?"
WORDNUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "half a": .5}

def balance_answer(q, emp_id):
    ql = q.lower()
    is_req = re.search(REQ_RE, ql) and re.search(r"\b(off|pto|vacation|leave)\b", ql)
    is_bal = re.search(TIME_RE, ql) and re.search(BAL_RE, ql)
    if not (is_req or is_bal):
        return None
    e = EMPLOYEES.get(emp_id or "")
    if not e:
        return {"answer": "I can check your balance once I know who you are. Pick your name in the \"Signed in as\" box at the top (demo employees).",
                "status": "balance", "section": ""}
    first = e["name"].split()[0]
    if is_req:
        m = re.search(REQ_RE, ql)
        n = m.group(2); n = WORDNUM.get(n, None) if not n[0].isdigit() else float(n)
        unit = m.group(3)
        hours = n * (8 if unit.startswith("day") else 40 if unit.startswith("week") else 1)
        bal = e["sick_hours"] if "sick" in ql else e["pto_hours"]
        kind = "sick leave" if "sick" in ql else "PTO"
        if hours <= bal:
            a = (f"Yes, {first}. That's {hours:g} hours and you have {bal:g} hours of {kind}, leaving {bal-hours:g}. "
                 + ("Submit it in the portal at least 2 weeks ahead for 3+ days" if hours >= 24 else "Submit it in the portal at least 48 hours ahead")
                 + " (Handbook, Section 2.2 Requesting PTO)")
        else:
            short = hours - bal
            a = (f"Not fully, {first}. That's {hours:g} hours but you have {bal:g} hours of {kind}, so you're {short:g} short. "
                 f"You earn {accrual_rate(e):g} more PTO hours on {datetime.date.fromisoformat(EMP['next_accrual']):%b %-d}. "
                 f"Want to talk options with HR? Type \"talk to HR\". (Handbook, Section 2.1 Paid Time Off (PTO) Accrual)")
        return {"answer": a, "status": "balance", "section": "2.2 Requesting PTO"}
    parts = []
    if "sick" in ql:
        parts.append(f"{first}, you have {fmt_h(e['sick_hours'])} of sick leave left this year (40 per year, separate from PTO).")
        sec = "2.3 Sick Leave"
    else:
        parts.append(f"{first}, you have {fmt_h(e['pto_hours'])} of PTO. You earn {accrual_rate(e):g} hours each pay period; next on "
                     f"{datetime.date.fromisoformat(EMP['next_accrual']):%b %-d}.")
        proj = year_end_pto(e)
        if proj > CARRYOVER_MAX:
            parts.append(f"Heads up: you're on track for {proj:.1f} hours by Dec 31, and only {CARRYOVER_MAX:g} carry over, so plan to use about {proj-CARRYOVER_MAX:.0f} hours or lose them.")
        sec = "2.1 Paid Time Off (PTO) Accrual"
        if "sick" not in ql and re.search(r"\b(all|everything|both)\b", ql):
            parts.append(f"Sick leave: {e['sick_hours']:g} hours.")
    a = " ".join(parts) + f" (Handbook, Section {sec})"
    return {"answer": a, "status": "balance", "section": sec}

# ---------- answering ----------
PROMPT = """You are the HR policy assistant for {company}. Answer the employee's question using ONLY the handbook sections below.
Rules: Be friendly and brief (under 60 words, it may be read as a text message). Use plain words. End with the citation in this form: (Handbook, Section <number> <title>).
If the sections do not answer the question, reply exactly: NOT_FOUND. Never guess, never give legal or medical advice.

Handbook sections:
{context}

Question: {question}"""

def llm(question, hits):
    context = "\n\n".join(f"Section {h['title']}: {h['text']}" for _, h in hits)
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                      json={"model": OPENAI_MODEL, "temperature": 0.1,
                            "messages": [{"role": "user", "content": PROMPT.format(company=COMPANY, context=context, question=question)}]},
                      timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def extractive(question, hits):
    """No-API fallback: return the best-matching sentences of the top section."""
    sec = hits[0][1]; qt = set(tokens(expand(question)))
    sents = re.split(r"(?<=[.!?])\s+", sec["text"])
    best = sorted(sents, key=lambda s: -len(qt & set(tokens(s))))[:2]
    best = [s for s in sents if s in best]
    return " ".join(best) + f" (Handbook, Section {sec['title']})"

def answer(question, channel="web", sender="anonymous", emp_id=None):
    q = question.strip()
    if not q:
        return {"answer": "Hi! Ask me anything about PTO, benefits, pay, or company policy.", "status": "greeting"}
    if re.fullmatch(r"(hi|hello|hey|help|start)[!. ]*", q.lower()):
        return {"answer": f"Hi! I'm the {COMPANY} HR assistant. Ask me about PTO, benefits, pay, holidays, or policies. To talk with HR, text TALK.", "status": "greeting"}
    topic = classify(q)
    if not topic and re.search(r"^talk\W*$|\b(talk|speak|meet)\b.{0,25}\b(hr|someone|somebody|person|human|rep)\b|\bappointment\b|\brepresentative\b|\breal person\b", q.lower()):
        a = f"Happy to connect you. Pick a time with HR here: {BOOKING_URL}"
        log(channel, sender, q, a, "", "booking"); return {"answer": a, "status": "booking"}
    if not topic:
        b = balance_answer(q, emp_id)
        if b:
            who = EMPLOYEES.get(emp_id or "", {}).get("name", sender)
            log(channel, who, q, b["answer"], b["section"], "balance"); return b

    if topic:
        alert_hr(topic, q, sender, channel)
        if topic == "personal crisis":
            a = "I'm so sorry you're going through this. If you're in danger, call or text 988 now. I've also alerted HR, and the free confidential EAP line is open 24/7 at 1-800-555-0199."
        elif topic in URGENT:
            a = f"Thank you for telling us. This needs a real person, so I've alerted HR and someone will contact you today. Retaliation for reporting is prohibited (Handbook, Section 5.2). If you're in immediate danger, call 911."
        else:
            a = f"That's best handled one-on-one, so I've let HR know. You can also pick a time with HR here: {BOOKING_URL}"
        log(channel, sender, q, a, "", f"handoff: {topic}")
        return {"answer": a, "status": "handoff", "topic": topic}

    hits = search(q)
    if hits:
        qt = set(tokens(q)); vocab = DOCS[SECTIONS.index(hits[0][1])]
        syn_hit = bool((set(tokens(expand(q))) - qt) & set(vocab))  # a synonym matched
        coverage = len([t for t in qt if t in vocab]) / max(1, len(qt))
    # Without an AI key, refuse weak matches; with a key, the model returns NOT_FOUND itself.
    if not hits or hits[0][0] < 1.0 or (not OPENAI_KEY and coverage < 0.5 and not syn_hit):
        a = f"I couldn't find that in the handbook, so I've noted it for HR. To ask them directly, pick a time here: {BOOKING_URL}"
        log(channel, sender, q, a, "", "unanswered"); return {"answer": a, "status": "unanswered"}
    try:
        a = llm(q, hits) if OPENAI_KEY else extractive(q, hits)
    except Exception:
        a = extractive(q, hits)
    if a.strip() == "NOT_FOUND":
        a = f"I couldn't find that in the handbook, so I've noted it for HR. To ask them directly, pick a time here: {BOOKING_URL}"
        log(channel, sender, q, a, "", "unanswered"); return {"answer": a, "status": "unanswered"}
    log(channel, sender, q, a, hits[0][1]["title"], "answered")
    return {"answer": a, "status": "answered", "section": hits[0][1]["title"]}

# ---------- routes ----------
@app.route("/")
def chat():
    return render_template("chat.html", company=COMPANY, employees=list(EMPLOYEES.values()))

@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(answer(data.get("question", ""), "web", data.get("sender", "web user"), data.get("employee_id")))

@app.route("/api/employees")
def api_employees():
    return jsonify([{"id": e["id"], "name": e["name"], "title": e["title"]} for e in EMPLOYEES.values()])

@app.route("/sms", methods=["POST"])
def sms():
    res = answer(request.form.get("Body", ""), "sms", request.form.get("From", "unknown"))
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(res["answer"])}</Message></Response>'
    return Response(xml, mimetype="text/xml")

@app.route("/hr")
def hr():
    with db() as c:
        rows = c.execute("select ts,channel,question,answer,section,status from log order by id desc limit 200").fetchall()
        top = c.execute("select section,count(*) n from log where status='answered' group by section order by n desc limit 8").fetchall()
        counts = dict(c.execute("select case when status like 'handoff%' then 'handoff' else status end s,count(*) from log group by s").fetchall())
    total = sum(counts.values())
    staff = [dict(e, rate=accrual_rate(e), year_end=year_end_pto(e), at_risk=max(0, round(year_end_pto(e) - CARRYOVER_MAX))) for e in EMPLOYEES.values()]
    return render_template("hr.html", rows=rows, top=top, counts=counts, total=total, company=COMPANY, staff=staff, as_of=EMP.get("as_of", ""))

@app.route("/qr")
def qr():
    """QR code pointing at this chat, for the demo slide or a breakroom poster."""
    import io, qrcode
    buf = io.BytesIO(); qrcode.make(request.host_url).save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/health")
def health():
    return {"ok": True, "sections": len(SECTIONS), "llm": bool(OPENAI_KEY), "employees": len(EMPLOYEES)}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
