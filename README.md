# HR Policy Assistant

Employees ask HR questions by web chat or text message. The assistant answers from the employee handbook, cites the exact section, and sends sensitive issues (harassment, safety, medical leave, pay disputes) straight to a real HR person. HR gets a live dashboard of what people ask most.

Built by Ayana Greer The sample company "Lone Star Logistics" and its handbook are fictional.

## What it does
- **Answers with a citation:** "(Handbook, Section 2.1 Paid Time Off (PTO) Accrual)"
- **Says "I don't know"** when the handbook doesn't cover it, and logs the gap for HR
- **Hands off sensitive topics:** harassment, discrimination, safety, FMLA and accommodations, pay disputes, and crisis language (points to 988 and the EAP)
- **Books time with HR:** "talk to HR" returns your booking page link
- **HR dashboard** at `/hr`: questions asked, answered without HR, handoffs, gaps, and top topics
- **QR code** at `/qr` for a slide or breakroom poster

## Pages
| Path | What it is |
|---|---|
| `/` | Employee chat |
| `/hr` | HR dashboard (refreshes every 15 seconds) |
| `/qr` | QR code linking to the chat |
| `/sms` | Twilio texting webhook |
| `/health` | Status check |

## Run it on your Mac
```bash
cd hr-policy-assistant
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="your key"          # optional; without it, answers quote the handbook directly
export HR_BOOKING_URL="your booking link"  # e.g. a Google Calendar appointment page
python3 app.py
```
Open https://hr-policy-assistant-mhh1.onrender.com for the chat and https://hr-policy-assistant-mhh1.onrender.com/hr for the dashboard.

## Put it online (free, Render)
1. Create a new GitHub repo named `hr-policy-assistant` and upload these files.
2. At render.com, choose **New > Blueprint**, pick the repo. It reads `render.yaml`.
3. Add `OPENAI_API_KEY` and `HR_BOOKING_URL` when asked.
4. Your chat lives at `https://<your-app>.onrender.com`.

Free Render apps sleep after 15 minutes idle and take about a minute to wake. Open the link a few minutes before you demo.

## Turn on texting (Twilio)
1. Upgrade to a paid Twilio account and buy a number.
2. Register it for US texting (A2P 10DLC, about 7 business days, or toll-free verification). Trial accounts can only text up to 5 verified phones.
3. In the number's settings, set **A message comes in** to `https://<your-app>.onrender.com/sms` (HTTP POST).

## Use a real company's handbook
Replace `handbook.md`. Keep each policy under its own `## <number> <title>` heading so citations stay clean.

## Settings
| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Writes friendly short answers. Optional |
| `OPENAI_MODEL` | Default `gpt-4o-mini` |
| `HR_BOOKING_URL` | Link sent when someone wants to talk to HR |
| `HR_ALERT_WEBHOOK` | Slack or Teams incoming webhook for live handoff alerts. Optional |
| `COMPANY_NAME` | Name shown in the chat |

## Demo script (3 minutes)
1. **Problem (20 sec):** HR answers the same PTO and benefits questions all day, and frontline workers can't easily reach HR.
2. **Scan the QR (60 sec):** the room asks real questions. Answers cite the handbook.
3. **The handoff (40 sec):** type "My manager keeps making comments about me." It refuses to handle it alone and alerts HR.
4. **Dashboard (40 sec):** show `/hr` filling up live: top topics and questions the handbook missed.
5. **Close (20 sec):** next step is connecting to UKG or Workday so it can answer "How much PTO do I have left?"
