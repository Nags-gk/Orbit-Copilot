"""
AI-Powered Transaction Categorizer

Provides intelligent category assignment for transactions using:
1. Keyword-based matching (~80 merchant/keyword mappings) — works offline
2. Optional Gemini 2.5 Flash fallback for unknown descriptions

Usage:
    from .categorizer import auto_categorize
    result = auto_categorize("Uber ride to airport")
    # {"category": "Transport", "confidence": 0.95, "method": "keyword"}
"""
from __future__ import annotations
import os
import re
import json
from typing import Optional


# ── Merchant / Keyword → Category Mapping ──────────────────
# Each entry: (pattern, category, confidence)
# Patterns are case-insensitive regexes

CATEGORY_RULES: list[tuple[str, str, float]] = [
    # ─── Food & Dining ───
    (r"\b(chipotle|mcdonalds|mcdonald's|burger king|wendy's|taco bell|subway|chick-fil-a)\b", "Food", 0.98),
    (r"\b(starbucks|dunkin|peet's|coffee bean|tim hortons|dutch bros)\b", "Food", 0.97),
    (r"\b(whole foods|trader joe's|safeway|kroger|publix|aldi|costco|walmart grocery|target grocery)\b", "Food", 0.95),
    (r"\b(doordash|uber eats|grubhub|postmates|instacart|caviar|seamless)\b", "Food", 0.95),
    (r"\b(pizza|sushi|thai|chinese|indian|mexican|italian|ramen|poke|bbq|burrito)\b", "Food", 0.85),
    (r"\b(restaurant|cafe|diner|bistro|eatery|grill|bakery|deli|brunch|lunch|dinner|breakfast|coffee)\b", "Food", 0.85),
    (r"\b(groceries|grocery|supermarket|food|meal|eat)\b", "Food", 0.80),
    (r"\b(dominos|papa john's|panera|chili's|applebee's|olive garden|red lobster|outback)\b", "Food", 0.95),
    (r"\b(wingstop|five guys|shake shack|in-n-out|popeyes|kfc|jack in the box|arby's)\b", "Food", 0.95),
    (r"\b(7eleven|7-eleven|wawa|sheetz|circle k)\b", "Food", 0.80),
    (r"\b(house of bagels|bagel|smoothie|juice|boba|tea)\b", "Food", 0.85),
    
    # ─── Transport ───
    (r"\b(uber|lyft|taxi|cab|rideshare)\b", "Transport", 0.95),
    (r"\b(shell|chevron|exxon|mobil|bp|sunoco|marathon|valero|arco|texaco|gasoline|gas station)\b", "Transport", 0.93),
    (r"\b(amtrak|greyhound|megabus|flixbus)\b", "Transport", 0.95),
    (r"\b(united airlines?|delta|american airlines?|southwest|jetblue|frontier|spirit|alaska air)\b", "Transport", 0.95),
    (r"\b(airline|flight|airfare|airport|boarding pass)\b", "Transport", 0.90),
    (r"\b(parking|toll|metro|subway|bus|train|transit|commut|fare|mta|bart|wmata)\b", "Transport", 0.90),
    (r"\b(car wash|auto repair|mechanic|tire|oil change|car insurance|geico|progressive|state farm)\b", "Transport", 0.85),
    (r"\b(hertz|enterprise|avis|budget rent|national car|zipcar|turo)\b", "Transport", 0.90),
    
    # ─── Utilities ───
    (r"\b(electric|electricity|power|pg&e|con edison|duke energy|southern company)\b", "Utilities", 0.95),
    (r"\b(water|sewer|sewage|waste management|garbage|trash)\b", "Utilities", 0.93),
    (r"\b(internet|comcast|xfinity|spectrum|at&t|verizon fios|cox|frontier comm)\b", "Utilities", 0.93),
    (r"\b(phone|t-mobile|tmobile|verizon wireless|at&t wireless|sprint|mint mobile|cricket)\b", "Utilities", 0.90),
    (r"\b(natural gas|gas bill|heating|hvac)\b", "Utilities", 0.90),
    (r"\b(rent|mortgage|lease|landlord|property management)\b", "Utilities", 0.85),
    (r"\b(utility|utilities|bill pay)\b", "Utilities", 0.80),
    (r"\b(insurance|home insurance|renter's insurance|allstate|nationwide)\b", "Utilities", 0.80),
    
    # ─── Entertainment ───
    (r"\b(netflix|hulu|disney\+?|hbo|max|peacock|paramount\+?|apple tv|crunchyroll|funimation)\b", "Entertainment", 0.97),
    (r"\b(spotify|apple music|tidal|pandora|soundcloud|youtube music|audible|kindle unlimited)\b", "Entertainment", 0.95),
    (r"\b(playstation|xbox|nintendo|steam|epic games|riot|roblox|twitch)\b", "Entertainment", 0.93),
    (r"\b(movie|cinema|theater|theatre|concert|show|event|ticket|ticketmaster|stubhub|eventbrite|fandango)\b", "Entertainment", 0.90),
    (r"\b(museum|zoo|aquarium|theme park|amusement|bowling|arcade|mini golf|escape room)\b", "Entertainment", 0.88),
    (r"\b(gym|fitness|yoga|peloton|crossfit|planet fitness|equinox|orangetheory|classpass)\b", "Entertainment", 0.85),
    (r"\b(book|magazine|newspaper|comics|manga|nyt|new york times|wall street journal)\b", "Entertainment", 0.80),
    (r"\b(game|gaming|entertainment|fun|hobby|recreation)\b", "Entertainment", 0.75),
    
    # ─── Shopping ───
    (r"\b(amazon|amzn|ebay|etsy|walmart|target|costco|sam's club|bj's)\b", "Shopping", 0.90),
    (r"\b(best buy|apple store|microsoft store|b&h|micro center|newegg)\b", "Shopping", 0.93),
    (r"\b(nike|adidas|zara|h&m|uniqlo|gap|old navy|nordstrom|macy's|tj maxx|marshalls|ross)\b", "Shopping", 0.93),
    (r"\b(home depot|lowes|lowe's|ikea|wayfair|pottery barn|crate & barrel|bed bath)\b", "Shopping", 0.90),
    (r"\b(cvs|walgreens|rite aid|pharmacy)\b", "Shopping", 0.85),
    (r"\b(sephora|ulta|bath & body|victoria's secret)\b", "Shopping", 0.90),
    (r"\b(shop|store|purchase|buy|bought|order|retail|mall|outlet)\b", "Shopping", 0.70),
    (r"\b(creatine|supplements?|vitamins?|protein)\b", "Shopping", 0.80),
    
    # ─── Subscription ───
    (r"\b(subscription|membership|monthly fee|annual fee|recurring)\b", "Subscription", 0.85),
    (r"\b(adobe|slack|notion|canva|dropbox|google workspace|microsoft 365|zoom|chatgpt)\b", "Subscription", 0.93),
    (r"\b(aws|azure|heroku|vercel|netlify|digital ocean|cloudflare)\b", "Subscription", 0.90),
    (r"\b(github|gitlab|jira|confluence|figma|sketch|invision)\b", "Subscription", 0.90),
    (r"\b(icloud|google one|onedrive)\b", "Subscription", 0.90),
    
    # ─── Income ───
    (r"\b(salary|payroll|paycheck|direct deposit|wage)\b", "Income", 0.98),
    (r"\b(dividend|interest earned|investment return|capital gain|royalt)\b", "Income", 0.95),
    (r"\b(refund|cashback|rebate|reimbursement)\b", "Income", 0.90),
    (r"\b(freelance|consulting|contract|side hustle|gig)\b", "Income", 0.85),
    (r"\b(transfer from|deposit|received|income|earned|paid me)\b", "Income", 0.80),
    (r"\b(venmo|zelle|paypal|cash app).*(?:from|received|credit)\b", "Income", 0.80),
]

# Compiled patterns for performance
_COMPILED_RULES = [(re.compile(pattern, re.IGNORECASE), cat, conf) for pattern, cat, conf in CATEGORY_RULES]


def auto_categorize(description: str, fallback_to_ai: bool = True) -> dict:
    """
    Auto-categorize a transaction description.
    
    Returns:
        {
            "category": "Food",
            "confidence": 0.95,
            "method": "keyword" | "ai" | "default",
            "alternatives": [{"category": "Shopping", "confidence": 0.7}]
        }
    """
    if not description or not description.strip():
        return {"category": "Shopping", "confidence": 0.1, "method": "default", "alternatives": []}
    
    # Phase 1: Keyword matching
    matches = []
    for compiled_re, category, base_confidence in _COMPILED_RULES:
        match = compiled_re.search(description)
        if match:
            # Boost confidence for longer matches (more specific)
            match_len = len(match.group())
            boost = min(0.05, match_len / 100)
            confidence = min(1.0, base_confidence + boost)
            matches.append({"category": category, "confidence": round(confidence, 3)})
    
    if matches:
        # Sort by confidence descending, deduplicate by category (keep highest)
        seen = {}
        for m in matches:
            if m["category"] not in seen or m["confidence"] > seen[m["category"]]:
                seen[m["category"]] = m["confidence"]
        
        sorted_matches = sorted(seen.items(), key=lambda x: x[1], reverse=True)
        best_cat, best_conf = sorted_matches[0]
        alternatives = [{"category": c, "confidence": conf} for c, conf in sorted_matches[1:3]]
        
        return {
            "category": best_cat,
            "confidence": best_conf,
            "method": "keyword",
            "alternatives": alternatives,
        }
    
    # Phase 2: Gemini AI fallback (if enabled and API key present)
    if fallback_to_ai:
        result = _gemini_categorize(description)
        if result:
            return result
    
    # Phase 3: Default fallback
    return {"category": "Shopping", "confidence": 0.1, "method": "default", "alternatives": []}


def _gemini_categorize(description: str) -> Optional[dict]:
    """Use Gemini 2.5 Flash or Local LLM to categorize an unknown transaction."""
    from .local_llm import settings
    
    prompt = (
        f"Categorize this financial transaction description into EXACTLY one of these categories: "
        f"Food, Transport, Utilities, Entertainment, Shopping, Subscription, Income.\n\n"
        f"Transaction: \"{description}\"\n\n"
        f"Respond with ONLY a JSON object: {{\"category\": \"...\", \"confidence\": 0.0-1.0}}"
    )
    
    if settings.use_local_llm:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=settings.local_model_url, api_key="local")
            response = client.chat.completions.create(
                model=settings.local_model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            result_text = response.choices[0].message.content
            if result_text:
                from .local_llm import clean_json_response
                cleaned_text = clean_json_response(result_text)
                data = json.loads(cleaned_text)
                valid_categories = {"Food", "Transport", "Utilities", "Entertainment", "Shopping", "Subscription", "Income"}
                cat = data.get("category", "Shopping")
                if cat not in valid_categories:
                    cat = "Shopping"
                return {
                    "category": cat,
                    "confidence": min(1.0, float(data.get("confidence", 0.7))),
                    "method": "local_ai",
                    "alternatives": [],
                }
        except Exception as e:
            print(f"Local AI categorization failed: {e}")
            return None
            
    # Fallback to Gemini if not local
    api_key = settings.gemini_api_key
    if not api_key:
        return None
    
    try:
        from google import genai
        from google.genai import types
        
        client = genai.Client(api_key=api_key)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
        )
        
        if response.text:
            data = json.loads(response.text)
            valid_categories = {"Food", "Transport", "Utilities", "Entertainment", "Shopping", "Subscription", "Income"}
            cat = data.get("category", "Shopping")
            if cat not in valid_categories:
                cat = "Shopping"
            return {
                "category": cat,
                "confidence": min(1.0, float(data.get("confidence", 0.7))),
                "method": "gemini_ai",
                "alternatives": [],
            }
    except Exception as e:
        print(f"Gemini categorization fallback failed: {e}")
    
    return None


def bulk_categorize(descriptions: list[str]) -> list[dict]:
    """Categorize multiple descriptions at once."""
    return [auto_categorize(desc) for desc in descriptions]
