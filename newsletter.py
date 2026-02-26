#!/usr/bin/env python3
"""
H Holdings Daily Construction & Real Estate Newsletter
Fetches real data from free sources and sends a beautiful HTML email daily.
"""

import smtplib
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import json
import re
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from html import unescape

# ─────────────────────────────────────────────
# CONFIGURATION — Edit these values
# ─────────────────────────────────────────────
GMAIL_ADDRESS   = os.environ.get("GMAIL_ADDRESS",   "your_gmail@gmail.com")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASS",  "xxxx xxxx xxxx xxxx")
RECIPIENTS      = ["brad@hholdings.us", "info@hholdings.us"]
SEND_HOUR_ET    = 7   # Send at 7:00 AM Eastern (used in scheduler)

# ─────────────────────────────────────────────
# RSS FEED SOURCES  (all free, no API key)
# ─────────────────────────────────────────────
RSS_FEEDS = {
    "raleigh_news": [
        {
            "name": "WRAL – Local News",
            "url":  "https://www.wral.com/rss/1148/",
        },
        {
            "name": "News & Observer – Real Estate",
            "url":  "https://www.newsobserver.com/news/business/real-estate-news/?outputType=atom",
        },
        {
            "name": "Triangle Business Journal",
            "url":  "https://www.bizjournals.com/triangle/rssfeed/breaking_news/",
        },
    ],
    "construction_news": [
        {
            "name": "Construction Dive",
            "url":  "https://www.constructiondive.com/feeds/news/",
        },
        {
            "name": "Builder Online",
            "url":  "https://www.builderonline.com/rss",
        },
        {
            "name": "NAHB Now",
            "url":  "https://nahbnow.com/feed/",
        },
    ],
    "materials_news": [
        {
            "name": "AGC – Construction Industry News",
            "url":  "https://www.agc.org/news/rss",
        },
        {
            "name": "Probuilder – Industry",
            "url":  "https://www.probuilder.com/rss.xml",
        },
    ],
}

# Keywords to filter Raleigh-relevant stories
RALEIGH_KEYWORDS = [
    "raleigh", "durham", "chapel hill", "wake county", "triangle",
    "cary", "apex", "morrisville", "holly springs", "fuquay",
    "downtown", "north carolina", "nc ", " nc,"
]

MORTGAGE_KEYWORDS = [
    "mortgage", "30-year", "interest rate", "fed", "federal reserve",
    "housing market", "home price", "refinance"
]

MATERIAL_KEYWORDS = [
    "lumber", "wood", "steel", "concrete", "copper", "material",
    "supply chain", "tariff", "price increase", "construction cost",
    "framing", "drywall", "roofing", "insulation"
]

# ─────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────

def fetch_rss(url, timeout=10):
    """Fetch and parse an RSS feed. Returns list of {title, link, description, pubdate}."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; HHoldingsNewsletter/1.0)"}
    try:
        req  = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        # Handle both RSS and Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        
        # Try RSS format first
        for item in root.findall(".//item")[:8]:
            def get(tag):
                el = item.find(tag)
                return unescape(el.text.strip()) if el is not None and el.text else ""
            desc = get("description")
            # Strip HTML tags from description
            desc = re.sub(r"<[^>]+>", "", desc)[:220].strip()
            items.append({
                "title": get("title"),
                "link":  get("link"),
                "desc":  desc,
                "date":  get("pubDate")[:16] if get("pubDate") else "",
            })
        
        # Try Atom format
        if not items:
            for entry in root.findall(".//atom:entry", ns)[:8]:
                def getat(tag):
                    el = entry.find(f"atom:{tag}", ns)
                    return unescape(el.text.strip()) if el is not None and el.text else ""
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                desc = getat("summary") or getat("content")
                desc = re.sub(r"<[^>]+>", "", desc)[:220].strip()
                items.append({
                    "title": getat("title"),
                    "link":  link,
                    "desc":  desc,
                    "date":  getat("updated")[:10] if getat("updated") else "",
                })
        return items
    except Exception as e:
        print(f"  ⚠️  Could not fetch {url}: {e}")
        return []


def fetch_mortgage_rate():
    """
    Fetch current 30-year mortgage rate from Freddie Mac's public JSON.
    Falls back to FRED (Federal Reserve) API (free, no key needed for this endpoint).
    """
    # Try Freddie Mac PMMS data (public)
    try:
        url = "https://www.freddiemac.com/pmms/docs/HistoricalWeeklyData.xlsx"
        # Freddie Mac xlsx is binary — instead use their JSON endpoint
        url = "https://www.freddiemac.com/pmms/pmms.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        # Latest entry
        latest = data.get("pmms30", [{}])[-1]
        rate = latest.get("pmms", "N/A")
        week = latest.get("weeklyendingdate", "")
        return {
            "rate":    f"{rate}%",
            "week":    week,
            "source":  "Freddie Mac Primary Mortgage Market Survey",
            "link":    "https://www.freddiemac.com/pmms",
            "change":  ""
        }
    except Exception as e:
        print(f"  ⚠️  Freddie Mac rate fetch failed: {e}")

    # Fallback: scrape Mortgage News Daily headline rate via their RSS
    try:
        items = fetch_rss("https://www.mortgagenewsdaily.com/rss/mortgage-rates")
        if items:
            first = items[0]
            # Try to extract rate from title like "30 Year Fixed Rate at 6.87%"
            match = re.search(r"(\d+\.\d+)%", first["title"] + first["desc"])
            if match:
                return {
                    "rate":   f"{match.group(1)}%",
                    "week":   datetime.now().strftime("%B %d, %Y"),
                    "source": "Mortgage News Daily",
                    "link":   "https://www.mortgagenewsdaily.com/mortgage-rates/30-year-fixed",
                    "change": first["title"]
                }
    except Exception:
        pass

    # Last fallback — show link only
    return {
        "rate":   "See source →",
        "week":   datetime.now().strftime("%B %d, %Y"),
        "source": "Bankrate",
        "link":   "https://www.bankrate.com/mortgages/30-year-mortgage-rate/",
        "change": "Click to see today's current rate"
    }


def filter_articles(articles, keywords, max_items=4):
    """Return articles whose title or desc contains any keyword."""
    matched, rest = [], []
    kw_lower = [k.lower() for k in keywords]
    for a in articles:
        text = (a["title"] + " " + a["desc"]).lower()
        if any(k in text for k in kw_lower):
            matched.append(a)
        else:
            rest.append(a)
    combined = matched + rest
    return combined[:max_items]


def gather_news():
    """Fetch all RSS feeds and organise into sections."""
    print("📡 Fetching news feeds...")
    
    raleigh_articles    = []
    construction_articles = []
    materials_articles  = []

    # Raleigh / local news
    for feed in RSS_FEEDS["raleigh_news"]:
        print(f"  → {feed['name']}")
        items = fetch_rss(feed["url"])
        for item in items:
            item["source"] = feed["name"]
        raleigh_articles.extend(items)

    # Construction news — also pull mortgage-related items
    mortgage_articles = []
    for feed in RSS_FEEDS["construction_news"]:
        print(f"  → {feed['name']}")
        items = fetch_rss(feed["url"])
        for item in items:
            item["source"] = feed["name"]
        construction_articles.extend(items)
        # Mine for mortgage news too
        mortgage_articles.extend(filter_articles(items, MORTGAGE_KEYWORDS, max_items=2))

    # Materials news
    for feed in RSS_FEEDS["materials_news"]:
        print(f"  → {feed['name']}")
        items = fetch_rss(feed["url"])
        for item in items:
            item["source"] = feed["name"]
        materials_articles.extend(items)

    return {
        "raleigh":      filter_articles(raleigh_articles, RALEIGH_KEYWORDS, max_items=5),
        "construction": filter_articles(construction_articles, MATERIAL_KEYWORDS + ["home", "build", "permit", "housing"], max_items=4),
        "materials":    filter_articles(materials_articles, MATERIAL_KEYWORDS, max_items=4),
        "mortgage_news": mortgage_articles[:3],
    }


# ─────────────────────────────────────────────
# HTML EMAIL BUILDER
# ─────────────────────────────────────────────

def build_email_html(mortgage, news):
    today = datetime.now().strftime("%A, %B %d, %Y")
    
    def article_cards(articles, fallback_msg="No new stories today."):
        if not articles:
            return f'<p style="color:#888;font-style:italic;padding:12px 0">{fallback_msg}</p>'
        cards = []
        for a in articles:
            source_badge = f'<span style="font-size:11px;color:#fff;background:#1a4f8a;padding:2px 8px;border-radius:20px;font-weight:600;letter-spacing:.3px">{a.get("source","")}</span>' if a.get("source") else ""
            desc_html = f'<p style="margin:6px 0 0;color:#555;font-size:13.5px;line-height:1.6">{a["desc"]}</p>' if a.get("desc") else ""
            cards.append(f"""
            <div style="background:#fff;border:1px solid #e8edf2;border-left:4px solid #1a6fc4;border-radius:6px;padding:14px 16px;margin-bottom:12px">
              <div style="margin-bottom:6px">{source_badge}</div>
              <h3 style="margin:0;font-size:15px;line-height:1.4">
                <a href="{a['link']}" target="_blank"
                   style="color:#1a4f8a;text-decoration:none;font-weight:700">{a['title']}</a>
              </h3>
              {desc_html}
            </div>""")
        return "".join(cards)

    # Mortgage rate box
    rate_color = "#1a6fc4"
    
    mortgage_news_html = ""
    if news.get("mortgage_news"):
        mortgage_news_html = f"""
        <div style="margin-top:16px">
          <p style="font-weight:700;color:#333;margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.5px">Related Market News</p>
          {article_cards(news['mortgage_news'][:2])}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>H Holdings Daily Brief – {today}</title>
</head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Georgia',serif">

<!-- WRAPPER -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8;padding:24px 0">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%">

  <!-- HEADER -->
  <tr><td style="background:linear-gradient(135deg,#0d2f5e 0%,#1a6fc4 100%);border-radius:10px 10px 0 0;padding:32px 36px 28px">
    <p style="margin:0 0 4px;color:#7eb8f7;font-size:12px;letter-spacing:2px;text-transform:uppercase;font-family:Arial,sans-serif">H Holdings</p>
    <h1 style="margin:0 0 6px;color:#fff;font-size:26px;font-weight:700;line-height:1.2">Daily Construction &amp; Real Estate Brief</h1>
    <p style="margin:0;color:#a8cef5;font-size:14px;font-family:Arial,sans-serif">{today}</p>
  </td></tr>

  <!-- BODY -->
  <tr><td style="background:#fff;padding:0 36px 28px">

    <!-- MORTGAGE RATE HERO -->
    <div style="background:linear-gradient(135deg,#f0f7ff 0%,#dceeff 100%);border:1px solid #b8d9f8;border-radius:8px;padding:22px 24px;margin:24px 0 28px">
      <p style="margin:0 0 4px;color:#1a4f8a;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;font-family:Arial,sans-serif">📈 30-Year Fixed Mortgage Rate</p>
      <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
        <span style="font-size:44px;font-weight:700;color:{rate_color};line-height:1;font-family:Arial,sans-serif">{mortgage['rate']}</span>
        <div>
          <p style="margin:0;color:#555;font-size:13px;font-family:Arial,sans-serif">Weekly Average · {mortgage['week']}</p>
          <p style="margin:3px 0 0;font-size:12px;font-family:Arial,sans-serif"><a href="{mortgage['link']}" style="color:#1a6fc4;text-decoration:none">Source: {mortgage['source']} →</a></p>
        </div>
      </div>
      {f'<p style="margin:10px 0 0;color:#444;font-size:13px;font-family:Arial,sans-serif;font-style:italic">{mortgage["change"]}</p>' if mortgage.get("change") else ""}
    </div>

    <!-- RALEIGH NEWS -->
    <h2 style="color:#0d2f5e;font-size:18px;border-bottom:2px solid #1a6fc4;padding-bottom:8px;margin:0 0 16px;font-family:Arial,sans-serif">
      🏙️ Raleigh &amp; Downtown Real Estate
    </h2>
    {article_cards(news['raleigh'], "No Raleigh stories found today — check back tomorrow.")}

    <!-- BUILDING MATERIALS -->
    <h2 style="color:#0d2f5e;font-size:18px;border-bottom:2px solid #1a6fc4;padding-bottom:8px;margin:24px 0 16px;font-family:Arial,sans-serif">
      📦 Building Materials &amp; Pricing
    </h2>
    {article_cards(news['materials'], "No material pricing updates today.")}

    <!-- CONSTRUCTION NEWS -->
    <h2 style="color:#0d2f5e;font-size:18px;border-bottom:2px solid #1a6fc4;padding-bottom:8px;margin:24px 0 16px;font-family:Arial,sans-serif">
      🏗️ New Home Construction News
    </h2>
    {article_cards(news['construction'], "No construction news today.")}

    {mortgage_news_html}

  </td></tr>

  <!-- FOOTER -->
  <tr><td style="background:#0d2f5e;border-radius:0 0 10px 10px;padding:20px 36px;text-align:center">
    <p style="margin:0 0 4px;color:#7eb8f7;font-size:13px;font-family:Arial,sans-serif;font-weight:700">H Holdings</p>
    <p style="margin:0;color:#a8cef5;font-size:12px;font-family:Arial,sans-serif">Automated daily brief · Generated {today}</p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────
# EMAIL SENDER
# ─────────────────────────────────────────────

def send_email(html_body, subject, recipients, gmail_addr, gmail_pass):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"H Holdings Daily Brief <{gmail_addr}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    print(f"📧 Sending to {recipients}...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_addr, gmail_pass)
        server.sendmail(gmail_addr, recipients, msg.as_string())
    print("✅ Email sent successfully!")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run():
    print(f"\n{'='*50}")
    print(f"  H Holdings Newsletter  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    print("📊 Fetching mortgage rate...")
    mortgage = fetch_mortgage_rate()
    print(f"  → Rate: {mortgage['rate']}  ({mortgage['source']})")

    news    = gather_news()
    html    = build_email_html(mortgage, news)
    subject = f"H Holdings Daily Brief – {datetime.now().strftime('%A, %B %d')}"

    send_email(html, subject, RECIPIENTS, GMAIL_ADDRESS, GMAIL_APP_PASS)


if __name__ == "__main__":
    run()
