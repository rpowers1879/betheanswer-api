"""
Be The Answer — AI Visibility Score API
========================================
A lightweight FastAPI backend that:
1. Scrapes a business website for structure/content/technical signals
2. Queries ChatGPT to test if the business gets recommended
3. Returns a 0-100 score with category breakdown

Setup:
  pip install fastapi uvicorn httpx beautifulsoup4 openai python-dotenv
  
  Create a .env file:
    OPENAI_API_KEY=sk-your-key-here
    ALLOWED_ORIGINS=https://betheanswer.cloud,http://localhost:3000

Run:
  uvicorn main:app --host 0.0.0.0 --port 8000

Deploy:
  - Railway: connect GitHub repo, set env vars, done
  - Render: same flow
  - VPS: use systemd or Docker
"""

import os
import re
import json
import asyncio
import logging
from urllib.parse import urlparse
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,https://betheanswer.cloud").split(",")
CHATGPT_MODEL = "gpt-4o-mini"  # cheap + fast, ~$0.01 per score check
REQUEST_TIMEOUT = 20  # seconds for website scraping

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("score-api")

# ─── App ──────────────────────────────────────────────────────────────
app = FastAPI(title="AI Visibility Score API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ─── Models ───────────────────────────────────────────────────────────
class ScoreRequest(BaseModel):
    url: str
    business: str
    industry: str
    city: str
    email: str


class CategoryScores(BaseModel):
    structure: int      # 0-10
    content: int        # 0-10
    technical: int      # 0-10
    ai_visibility: int  # 0-10


class ScoreResponse(BaseModel):
    score: int           # 0-100
    categories: CategoryScores
    business: str
    url: str


# ─── Website Scraper ──────────────────────────────────────────────────
async def scrape_website(url: str) -> dict:
    """
    Scrape homepage and extract signals relevant to AI visibility.
    Returns a dict of raw signals for scoring.
    """
    signals = {
        "reachable": False,
        "https": url.startswith("https"),
        "title": "",
        "meta_description": "",
        "h1_count": 0,
        "h1_text": [],
        "h2_count": 0,
        "h2_text": [],
        "word_count": 0,
        "has_schema": False,
        "schema_types": [],
        "has_og_tags": False,
        "has_canonical": False,
        "has_robots": True,  # assume true unless blocked
        "internal_links": 0,
        "external_links": 0,
        "has_about_page": False,
        "has_contact_page": False,
        "has_blog": False,
        "image_count": 0,
        "images_with_alt": 0,
        "load_error": None,
    }

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "BeTheAnswer-ScoreBot/1.0"}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            signals["reachable"] = True
            html = resp.text

    except httpx.TimeoutException:
        signals["load_error"] = "timeout"
        return signals
    except Exception as e:
        signals["load_error"] = str(e)[:100]
        return signals

    # Parse HTML
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    signals["title"] = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    signals["meta_description"] = meta_desc.get("content", "") if meta_desc else ""

    # Headings
    h1s = soup.find_all("h1")
    signals["h1_count"] = len(h1s)
    signals["h1_text"] = [h.get_text(strip=True)[:100] for h in h1s[:5]]

    h2s = soup.find_all("h2")
    signals["h2_count"] = len(h2s)
    signals["h2_text"] = [h.get_text(strip=True)[:100] for h in h2s[:10]]

    # Body word count
    body = soup.find("body")
    if body:
        text = body.get_text(separator=" ", strip=True)
        signals["word_count"] = len(text.split())

    # Schema / JSON-LD
    schemas = soup.find_all("script", attrs={"type": "application/ld+json"})
    if schemas:
        signals["has_schema"] = True
        for s in schemas:
            try:
                data = json.loads(s.string)
                if isinstance(data, dict) and "@type" in data:
                    signals["schema_types"].append(data["@type"])
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "@type" in item:
                            signals["schema_types"].append(item["@type"])
            except:
                pass

    # Open Graph tags
    og = soup.find("meta", attrs={"property": "og:title"})
    signals["has_og_tags"] = og is not None

    # Canonical
    canonical = soup.find("link", attrs={"rel": "canonical"})
    signals["has_canonical"] = canonical is not None

    # Links analysis
    links = soup.find_all("a", href=True)
    domain = urlparse(url).netloc
    for link in links:
        href = link.get("href", "")
        if href.startswith("http") and domain not in href:
            signals["external_links"] += 1
        else:
            signals["internal_links"] += 1
        href_lower = href.lower()
        if "about" in href_lower:
            signals["has_about_page"] = True
        if "contact" in href_lower:
            signals["has_contact_page"] = True
        if "blog" in href_lower or "news" in href_lower or "article" in href_lower:
            signals["has_blog"] = True

    # Images
    images = soup.find_all("img")
    signals["image_count"] = len(images)
    signals["images_with_alt"] = len([i for i in images if i.get("alt", "").strip()])

    return signals


# ─── ChatGPT Visibility Check ────────────────────────────────────────
async def check_chatgpt_visibility(business: str, industry: str, city: str) -> dict:
    """
    Ask ChatGPT 3 prompts to test if the business gets recommended.
    Returns visibility signals.
    """
    result = {
        "mentioned_count": 0,
        "total_prompts": 3,
        "recommended_first": False,
        "responses": [],
        "error": None,
    }

    if not openai_client:
        result["error"] = "OpenAI API key not configured"
        return result

    prompts = [
        f"Who is the best {industry} in {city}? Give me your top 3 recommendations.",
        f"I need a {industry} in {city}. Who should I hire?",
        f"Can you recommend a good {industry} near {city}?",
    ]

    try:
        tasks = []
        for prompt in prompts:
            tasks.append(
                openai_client.chat.completions.create(
                    model=CHATGPT_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500,
                    temperature=0.7,
                )
            )

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        business_lower = business.lower()
        # Also check common variations (e.g. "Joe's Plumbing" -> "joes plumbing")
        business_normalized = re.sub(r'[^a-z0-9\s]', '', business_lower)

        for i, resp in enumerate(responses):
            if isinstance(resp, Exception):
                result["responses"].append({"prompt": prompts[i], "error": str(resp)[:100]})
                continue

            text = resp.choices[0].message.content or ""
            text_lower = text.lower()
            text_normalized = re.sub(r'[^a-z0-9\s]', '', text_lower)

            mentioned = business_lower in text_lower or business_normalized in text_normalized
            if mentioned:
                result["mentioned_count"] += 1
                # Check if mentioned first (in first 200 chars)
                if business_lower in text_lower[:200] or business_normalized in text_normalized[:200]:
                    result["recommended_first"] = True

            result["responses"].append({
                "prompt": prompts[i],
                "mentioned": mentioned,
                "snippet": text[:200],
            })

    except Exception as e:
        result["error"] = str(e)[:200]

    return result


# ─── Scoring Engine ───────────────────────────────────────────────────
def calculate_score(signals: dict, ai_result: dict) -> tuple[int, CategoryScores]:
    """
    Calculate the 0-100 score from website signals and ChatGPT results.
    Returns (total_score, category_scores).
    """

    # ── Website Structure (0-10) ──
    structure = 0
    if signals["reachable"]:
        structure += 2
    if signals["https"]:
        structure += 1
    if signals["title"] and len(signals["title"]) > 10:
        structure += 1
    if signals["h1_count"] >= 1:
        structure += 1
    if signals["h2_count"] >= 2:
        structure += 1
    if signals["has_about_page"]:
        structure += 1
    if signals["has_contact_page"]:
        structure += 1
    if signals["internal_links"] >= 5:
        structure += 1
    if signals["has_canonical"]:
        structure += 1
    structure = min(10, structure)

    # ── Content Signals (0-10) ──
    content = 0
    if signals["meta_description"] and len(signals["meta_description"]) > 50:
        content += 2
    if signals["word_count"] >= 300:
        content += 1
    if signals["word_count"] >= 800:
        content += 1
    if signals["word_count"] >= 1500:
        content += 1
    if signals["has_blog"]:
        content += 2
    if signals["h2_count"] >= 3:
        content += 1
    if len(signals["h2_text"]) >= 3:
        # Check if headings contain meaningful content
        avg_len = sum(len(h) for h in signals["h2_text"]) / len(signals["h2_text"])
        if avg_len > 15:
            content += 1
    if signals["has_og_tags"]:
        content += 1
    content = min(10, content)

    # ── Technical / Schema (0-10) ──
    technical = 0
    if signals["https"]:
        technical += 2
    if signals["has_schema"]:
        technical += 3
        # Bonus for specific schema types
        good_types = {"LocalBusiness", "Organization", "ProfessionalService", "Service", "Product"}
        if any(t in good_types for t in signals["schema_types"]):
            technical += 1
    if signals["has_canonical"]:
        technical += 1
    if signals["has_og_tags"]:
        technical += 1
    if signals["image_count"] > 0 and signals["images_with_alt"] > 0:
        alt_ratio = signals["images_with_alt"] / signals["image_count"]
        if alt_ratio > 0.5:
            technical += 1
    if not signals["load_error"]:
        technical += 1
    technical = min(10, technical)

    # ── ChatGPT Visibility (0-10) ──
    ai_vis = 0
    if ai_result.get("error") and not ai_result.get("mentioned_count"):
        # If API error, give a neutral score
        ai_vis = 2
    else:
        mentioned = ai_result.get("mentioned_count", 0)
        total = ai_result.get("total_prompts", 3)

        if mentioned == 0:
            ai_vis = 0
        elif mentioned == 1:
            ai_vis = 3
        elif mentioned == 2:
            ai_vis = 6
        elif mentioned >= 3:
            ai_vis = 8

        if ai_result.get("recommended_first"):
            ai_vis = min(10, ai_vis + 2)

    ai_vis = min(10, ai_vis)

    # ── Total Score (weighted) ──
    # ChatGPT visibility is weighted heaviest since that's what matters most
    total = round(
        (structure * 1.5) +   # 15% weight -> max 15
        (content * 2.0) +     # 20% weight -> max 20
        (technical * 2.5) +   # 25% weight -> max 25
        (ai_vis * 4.0)        # 40% weight -> max 40
    )
    total = max(0, min(100, total))

    categories = CategoryScores(
        structure=structure,
        content=content,
        technical=technical,
        ai_visibility=ai_vis,
    )

    return total, categories


# ─── API Endpoint ─────────────────────────────────────────────────────
@app.post("/api/score", response_model=ScoreResponse)
async def get_score(req: ScoreRequest):
    """
    Main scoring endpoint.
    Accepts a URL + business details, returns a 0-100 score.
    """
    logger.info(f"Score request: {req.business} ({req.url}) - {req.industry} in {req.city}")

    # Normalize URL
    url = req.url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    # Run scrape + ChatGPT check concurrently
    signals, ai_result = await asyncio.gather(
        scrape_website(url),
        check_chatgpt_visibility(req.business, req.industry, req.city),
    )

    logger.info(f"Scrape signals: reachable={signals['reachable']}, schema={signals['has_schema']}, words={signals['word_count']}")
    logger.info(f"AI result: mentioned={ai_result.get('mentioned_count', 0)}/{ai_result.get('total_prompts', 3)}")

    # Calculate score
    total, categories = calculate_score(signals, ai_result)

    logger.info(f"Final score: {total}/100 (struct={categories.structure}, content={categories.content}, tech={categories.technical}, ai={categories.ai_visibility})")

    # TODO: Store lead in database (email, business, url, score, timestamp)
    # This is where you'd save to PostgreSQL, Supabase, Airtable, Google Sheets, etc.
    logger.info(f"Lead captured: {req.email} - {req.business}")

    return ScoreResponse(
        score=total,
        categories=categories,
        business=req.business,
        url=url,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "openai_configured": bool(OPENAI_API_KEY)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
