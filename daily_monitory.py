#!/usr/bin/env python3
import os
import json
import urllib.parse
import smtplib
import logging
from datetime import datetime, timedelta
from email.message import EmailMessage

import feedparser
from openai import OpenAI

# ---------------------------------------------------------
# 1. PORTFOLIO & THESIS DEFINITIONS
# ---------------------------------------------------------
# We include the Ticker to pull statutory exchange announcements, 
# and the Thesis to guide the AI's ecosystem web searches.
PORTFOLIO = {
    "Laserbond": {
        "ticker": "LBL.AX",
        "thesis": "Surface engineering. Sensitive to mining capex, heavy manufacturing, equipment wear-and-tear, and specialized engineering labor."
    },
    "Cuscal": {
        "ticker": "CCL.AX", # Or None if unlisted/recently listed
        "thesis": "B2B payments. Sensitive to RBA/APRA regulations, NPP adoption, and credit union health."
    },
    "Praemium": {
        "ticker": "PPS.AX",
        "thesis": "Wealth admin platform. Sensitive to IFA migration, Hub24/Netwealth competition, and regulatory shifts in advice."
    },
    "Cogstate": {
        "ticker": "CGS.AX",
        "thesis": "Cognitive assessment. Sensitive to pharma R&D, specifically Alzheimer's FDA approvals and clinical trial budgets."
    },
    "Kip McGrath": {
        "ticker": "KME.AX",
        "thesis": "Education franchising. Sensitive to franchisee profitability, online tutoring competition, and household discretionary spend."
    },
    "Airtasker": {
        "ticker": "ART.AX",
        "thesis": "Gig-economy marketplace. Sensitive to contractor regulations, labor supply, and consumer spend."
    },
    "Motio": {
        "ticker": "MXO.AX",
        "thesis": "DOOH advertising in medical/sports. Sensitive to ad spend macro, venue foot traffic, and programmatic yield."
    },
    "The Property Franchise Group": {
        "ticker": "TPFG.L",
        "thesis": "UK property franchising. Sensitive to UK housing volumes, BoE rates, and Renters Reform Bill."
    },
    "Reckon": {
        "ticker": "RKN.AX",
        "thesis": "Accounting software. Sensitive to SME software churn, legal/accounting IT budgets, and Xero/MYOB competition."
    },
    "Fiducian": {
        "ticker": "FID.AX",
        "thesis": "Wealth management. Sensitive to IFA retention, M&A multiples, compliance costs, and FUM macro."
    },
    "Global Health": {
        "ticker": "GLH.AX",
        "thesis": "Healthcare EMR software. Sensitive to hospital IT procurement, and government health-tech mandates."
    },
    "Currency Exchange International": {
        "ticker": "CXI.TO",
        "thesis": "FX services. Sensitive to cross-border travel macro, physical banknote supply chains, and FX volatility."
    }
}

# ---------------------------------------------------------
# 2. SYSTEM CONFIGURATION
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY environment variable.")

client = OpenAI(api_key=OPENAI_API_KEY)
LOOKBACK_DAYS = 7 # 7 days is optimal for micro-caps so we don't miss slow-burn news


# ---------------------------------------------------------
# 3. HARVESTERS (Statutory + Ecosystem)
# ---------------------------------------------------------
def fetch_rss_articles(url: str, source_type: str) -> list[dict]:
    """Generic RSS fetcher filtering for the last X days."""
    feed = feedparser.parse(url)
    cutoff_date = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    
    articles = []
    for entry in feed.entries:
        try:
            # RSS date formats vary, feedparser usually standardizes to struct_time
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6])
            else:
                continue
                
            if pub_date >= cutoff_date:
                articles.append({
                    "title": entry.title,
                    "link": entry.link,
                    "source": source_type,
                    "date": pub_date.strftime("%Y-%m-%d")
                })
        except Exception:
            continue
    return articles

def get_statutory_news(ticker: str) -> list[dict]:
    """Pulls official exchange announcements and financial news via Yahoo Finance."""
    if not ticker: return []
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}"
    return fetch_rss_articles(url, source_type="Statutory/Exchange")

def get_ecosystem_news(query: str) -> list[dict]:
    """Pulls broader web news for competitors, supply chains, and macro via Google."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-AU&gl=AU&ceid=AU:en"
    return fetch_rss_articles(url, source_type="Web/Ecosystem")


# ---------------------------------------------------------
# 4. AI AGENTS (Query Generator & Signal Extractor)
# ---------------------------------------------------------
def generate_ecosystem_queries(company: str, thesis: str) -> list[str]:
    prompt = f"""
    You are a fundamental equity analyst researching '{company}'. 
    Investment context: {thesis}
    
    Generate exactly 3 Google News search queries to find material fundamental data.
    Focus on: 1. Competitor actions 2. Supply chain/macro 3. Regulatory shifts.
    Return ONLY a comma-separated list.
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    queries = response.choices[0].message.content.split(',')
    queries.append(f'"{company}"') # Always search the company name explicitly
    return [q.strip() for q in queries if q.strip()]

def filter_and_score_news(company: str, thesis: str, articles: list[dict]) -> dict:
    if not articles:
        return {"events": [], "synthesis": "No news detected in the past 7 days."}
        
    news_text = "\n".join([f"- [{a['source']}] {a['title']} (URL: {a['link']})" for a in articles])
    
    prompt = f"""
    You are a ruthless principal-capital allocator. You care about asymmetric risk and structural moats.
    Below is a 7-day raw feed of statutory announcements and ecosystem news for '{company}'.
    Thesis: {thesis}
    
    YOUR JOB:
    1. Discard all stock-price movements, broker upgrades, and PR fluff.
    2. Identify material fundamental events: Insider buying/selling (Appendix 3Y), contract wins/losses, competitor shifts, regulatory changes.
    3. Output pure JSON matching this exact structure:
    {{
        "synthesis": "A 1-2 sentence summary of the overarching fundamental shift this week (or state 'Status quo maintained' if nothing major happened).",
        "events": [
            {{
                "headline": "The event title",
                "category": "Insider Trading | Competitor | Regulatory | Macro | Commercial",
                "impact": "Bullish | Bearish | Neutral",
                "score": <integer 1-10 on materiality>,
                "rationale": "One sentence explaining WHY this impacts cash flows or moat.",
                "url": "the link provided in the text"
            }}
        ]
    }}
    
    RAW FEED:
    {news_text}
    """
    
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={ "type": "json_object" },
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {"events": [], "synthesis": "Error parsing AI output."}


# ---------------------------------------------------------
# 5. HTML DASHBOARD BUILDER
# ---------------------------------------------------------
def build_html_report(all_analyses: dict) -> str:
    html = """
    <html>
    <head>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f6f8; color: #1a1a1a; padding: 20px; }
            .container { max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
            h1 { border-bottom: 2px solid #2c3e50; padding-bottom: 10px; color: #2c3e50; }
            .company-card { margin-bottom: 30px; border: 1px solid #e1e4e8; border-radius: 6px; padding: 20px; }
            .company-header { display: flex; justify-content: space-between; align-items: center; margin-top: 0; margin-bottom: 10px; border-bottom: 1px solid #eee; padding-bottom: 10px;}
            .synthesis { font-style: italic; color: #555; font-size: 14px; margin-bottom: 15px; }
            .event { margin-bottom: 15px; padding: 12px; background: #f8fafc; border-left: 4px solid #cbd5e1; }
            .badge { display: inline-block; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; margin-right: 8px; text-transform: uppercase; }
            .bullish { background-color: #dcfce7; color: #0f5132; }
            .bearish { background-color: #fee2e2; color: #991b1b; }
            .neutral { background-color: #f1f5f9; color: #475569; }
            .score { font-weight: bold; color: #d97706; }
            a { color: #2563eb; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .no-news { color: #94a3b8; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Fundamental Radar 📡</h1>
            <p style="color: #64748b; font-size: 13px;">Automated Portfolio Ecosystem Monitor | Trailing 7 Days</p>
    """
    
    for company, data in all_analyses.items():
        html += f"<div class='company-card'>"
        html += f"<h2 class='company-header'>{company}</h2>"
        html += f"<div class='synthesis'>{data.get('synthesis', '')}</div>"
        
        events = data.get('events', [])
        if not events:
            html += "<p class='no-news'>No material fundamental events detected in the current noise.</p>"
        else:
            # Sort events by score descending
            events.sort(key=lambda x: x.get('score', 0), reverse=True)
            for e in events:
                impact = str(e.get('impact', 'Neutral')).lower()
                badge_class = "bullish" if impact == "bullish" else "bearish" if impact == "bearish" else "neutral"
                
                html += f"""
                <div class='event'>
                    <div style="margin-bottom: 5px;">
                        <span class="badge {badge_class}">{e.get('impact', 'Neutral')}</span>
                        <span class="badge" style="background:#e2e8f0; color:#334155;">{e.get('category', 'News')}</span>
                        <span class="score">Score: {e.get('score', '-')}</span>
                    </div>
                    <strong><a href="{e.get('url', '#')}" target="_blank">{e.get('headline', 'Untitled')}</a></strong>
                    <p style="margin: 5px 0 0 0; font-size: 13px; color: #334155;">{e.get('rationale', '')}</p>
                </div>
                """
        html += "</div>"
        
    html += "</div></body></html>"
    return html

def dispatch_email(html: str):
    env = {k: os.getenv(k) for k in ['SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'EMAIL_FROM', 'EMAIL_TO']}
    if not all(env.values()):
        logging.warning("Skipping email dispatch. Missing SMTP environment variables.")
        return
        
    smtp_port = int(os.getenv('SMTP_PORT') or '587')
    msg = EmailMessage()
    msg['Subject'] = f"📡 Fundamental Radar - {datetime.now().strftime('%A, %d %b')}"
    msg['From'] = env['EMAIL_FROM']
    msg['To'] = env['EMAIL_TO']
    msg.add_alternative(html, subtype='html')
            
    with smtplib.SMTP(env['SMTP_HOST'], smtp_port) as server:
        if smtp_port != 465: server.starttls()
        server.login(env['SMTP_USERNAME'], env['SMTP_PASSWORD'])
        server.send_message(msg)
    logging.info('Email dispatched successfully.')

# ---------------------------------------------------------
# 6. MAIN EXECUTION PIPELINE
# ---------------------------------------------------------
def run_monitor():
    logging.info("Starting Fundamental Radar...")
    all_analyses = {}
    
    for company, details in PORTFOLIO.items():
        logging.info(f"Scanning: {company}")
        ticker = details['ticker']
        thesis = details['thesis']
        
        # 1. Harvest Statutory & Exchange News
        articles = get_statutory_news(ticker)
        
        # 2. Harvest Ecosystem News
        queries = generate_ecosystem_queries(company, thesis)
        for q in queries:
            articles.extend(get_ecosystem_news(q))
            
        # Deduplicate
        seen = set()
        unique_articles = []
        for a in articles:
            if a['title'] not in seen:
                seen.add(a['title'])
                unique_articles.append(a)
                
        logging.info(f"Found {len(unique_articles)} raw headlines. Passing to AI Analyst...")
        
        # 3. Alpha Filter (LLM)
        analysis = filter_and_score_news(company, thesis, unique_articles)
        all_analyses[company] = analysis

    # 4. Build & Send
    report_html = build_html_report(all_analyses)
    
    with open("radar_preview.html", "w", encoding="utf-8") as f:
        f.write(report_html)
    logging.info("Saved local preview to radar_preview.html")
    
    dispatch_email(report_html)

if __name__ == "__main__":
    run_monitor()
