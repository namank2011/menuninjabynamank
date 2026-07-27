from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import sys
from pathlib import Path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas import MENU_JSON_SCHEMA, MenuExtraction

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
TEXT_MODEL = os.getenv("OLLAMA_TEXT_MODEL", "llama3.1:latest")
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava:latest")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT", "300"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SYSTEM_PROMPT = """
You are a strict, high-fidelity restaurant menu extraction engine for ShopVerse POS bulk upload.
Return ONLY valid JSON that matches the requested structure.
Do not add markdown, explanations, or comments.
Never invent, guess, autocomplete, or hallucinate menu items or prices. Use ONLY visible data present in the input file.
If a value is not visible, use an empty string or null.
""".strip()

MENU_EXTRACTION_PROMPT = """
Extract restaurant/cafe menu data from the input with 100% precision.

Structure:
{
  "currency": "INR",
  "document_notes": [],
  "items": [
    {
      "category": "category name",
      "product_name": "item name",
      "description": "short description",
      "dietary_tag": "Veg" or "Non-Veg" or "Egg" or "",
      "confidence": 0.9,
      "source_text": "raw snippet",
      "variations": [
        {"name": "Regular", "price": 99.0, "listing_price": 120.0}
      ]
    }
  ]
}

Rules:
1. Extract every visible menu item (food, drink, add-on option) with its category and price.
2. Category should come from nearby heading/section. If not found, use "Uncategorized".
3. Product name must be clean and must not include price, currency symbols, dots, or category name.
4. Prices must be numeric only. Remove ₹, Rs, /-, commas, and text.
5. If one item has variations/sizes/options with different prices, keep ONE item and put all variations inside variations[]:
   Example: Small 99, Medium 149, Large 199 => variations = [Small 99, Medium 149, Large 199]
6. If item has one price only, variations should contain one object with name="" and price=<price>.
7. Infer and identify the dietary_tag ("veg", "non veg", "egg") based on the product name and product description, even if it is not explicitly written (egg roll is "egg", chicken tikka is "non veg", paneer is "veg", etc.). Convert all values to lowercase in output.
8. Descriptions should be short and only if visible.
9. Keep spelling exactly as written on the menu. Under no circumstances attempt to autocomplete abbreviations or guess misspelt/cut-off characters.
10. Set confidence between 0 and 1 based on clarity of item name + price.
11. Include a short source_text snippet for review.
12. DO NOT extract the restaurant name, address, email, websites, phone/mobile numbers, GST/tax info, license info, opening hours, or social media handles as menu items. Focus strictly on food and beverage items.
13. Keep decimals exactly as written (e.g., 9.9 or 14.99). Never round prices to whole numbers.
14. Ensure side-by-side columns are aligned correctly. Do not misalign or swap lines when matching prices horizontally.
15. Extract the currency precisely (e.g. if ₹/Rs is used set "currency": "INR", if $ set "currency": "USD").
16. STRICT FIDELITY RULE: Never extrapolate or suggest menu items that are not directly present in the input text or image. Every extracted product name and price must exist in the input. If there are no items, return an empty items list.
17. NEVER use historical corrections guidelines or learned memory to invent products that are missing from the current input. Only apply corrections as formatting/naming guidelines when a matching item is actually present.

Return JSON only.
""".strip()


def _get_prompt_with_memory() -> str:
    try:
        from database import get_learned_corrections
        cat_memory = get_learned_corrections("category")
        prod_memory = get_learned_corrections("product_name")
    except Exception:
        cat_memory = {}
        prod_memory = {}
        
    guidelines = []
    
    if prod_memory:
        guidelines.append("--- Corrected Naming Guidelines (Learn and output cleanly like these examples when spelling matches) ---")
        for orig, corr in list(prod_memory.items())[:15]:
            guidelines.append(f'- Clean/Format "{orig}" -> output: "{corr}"')
            
    if cat_memory:
        guidelines.append("--- Corrected Category Guidelines (Learn classification preferences from these examples) ---")
        for orig, corr in list(cat_memory.items())[:15]:
            guidelines.append(f'- Classify item "{orig}" under category: "{corr}"')
            
    if guidelines:
        return MENU_EXTRACTION_PROMPT + "\n\n" + "\n".join(guidelines)
        
    return MENU_EXTRACTION_PROMPT


GEMINI_MENU_SCHEMA = {
    "type": "object",
    "properties": {
        "currency": {"type": "string"},
        "document_notes": {
            "type": "array", 
            "items": {"type": "string"}
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "product_name": {"type": "string"},
                    "description": {"type": "string"},
                    "dietary_tag": {"type": "string"},
                    "confidence": {"type": "number"},
                    "source_text": {"type": "string"},
                    "variations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "price": {"type": "number"},
                                "listing_price": {"type": "number"}
                            },
                            "required": ["name", "price"]
                        }
                    }
                },
                "required": [
                    "category", "product_name", "description", "dietary_tag", "variations"
                ]
            }
        }
    },
    "required": ["currency", "items"]
}


def _post_generate(payload: Dict[str, Any]) -> str:
    url = f"{OLLAMA_BASE_URL}/api/generate"
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        if err.response is not None and err.response.status_code == 404:
            raise ValueError(
                f"Ollama model '{payload.get('model')}' was not found. Please install/pull it "
                f"by running 'ollama pull {payload.get('model')}' in your command prompt."
            ) from err
        raise
    data = response.json()
    return data.get("response", "")


def _extract_json(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def parse_loose_menu_extraction(parsed: Any) -> MenuExtraction:
    if not isinstance(parsed, dict):
        if isinstance(parsed, list):
            items_raw = parsed
        else:
            items_raw = []
        currency = "INR"
        notes = []
    else:
        items_raw = parsed.get("items", [])
        if not isinstance(items_raw, list):
            items_raw = []
        currency = parsed.get("currency", "INR")
        notes = parsed.get("document_notes", [])
        if not isinstance(notes, list):
            notes = []

    standardized_items = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
            
        p_name = item.get("product_name") or item.get("name") or item.get("productName")
        if not p_name:
            continue
            
        category = item.get("category") or item.get("categoryName") or item.get("category_name") or "Uncategorized"
        desc = item.get("description") or item.get("desc") or ""
        diet = item.get("dietary_tag") or item.get("dietaryTag") or item.get("dietary") or ""
        if diet not in ["Veg", "Non-Veg", "Egg", ""]:
            l_diet = str(diet).lower()
            if "non" in l_diet:
                diet = "Non-Veg"
            elif "egg" in l_diet:
                diet = "Egg"
            elif "veg" in l_diet:
                diet = "Veg"
            else:
                diet = ""
                
        # Parse variations
        variations = []
        raw_vars = item.get("variations")
        if isinstance(raw_vars, list) and raw_vars:
            for v in raw_vars:
                if not isinstance(v, dict):
                    continue
                v_name = v.get("name") or v.get("variantName") or ""
                
                v_price = v.get("price") or v.get("sellingPrice") or v.get("selling_price")
                try:
                    v_price = float(v_price) if v_price is not None and str(v_price).strip() != "" else None
                except ValueError:
                    v_price = None
                    
                v_lp = v.get("listing_price") or v.get("listingPrice") or v.get("mrp") or v_price
                try:
                    v_lp = float(v_lp) if v_lp is not None and str(v_lp).strip() != "" else None
                except ValueError:
                    v_lp = None
                    
                variations.append({
                    "name": str(v_name),
                    "price": v_price,
                    "listing_price": v_lp
                })
        else:
            price = item.get("price") or item.get("sellingPrice") or item.get("selling_price")
            try:
                price = float(price) if price is not None and str(price).strip() != "" else None
            except ValueError:
                price = None
            variations = [{"name": "", "price": price, "listing_price": price}]
            
        standardized_items.append({
            "category": str(category),
            "product_name": str(p_name),
            "description": str(desc),
            "dietary_tag": diet,
            "confidence": item.get("confidence") or 0.9,
            "source_text": item.get("source_text") or "",
            "variations": variations
        })

    return MenuExtraction(
        currency=str(currency),
        document_notes=[str(n) for n in notes],
        items=standardized_items
    )


def extract_from_text_with_ollama(text: str, model: Optional[str] = None, api_key: Optional[str] = None, bypass_to_gemini: bool = True) -> MenuExtraction:
    active_key = (api_key or GEMINI_API_KEY) if bypass_to_gemini else None
    prompt_with_mem = _get_prompt_with_memory()
    if active_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={active_key}"
        prompt_text = f"{SYSTEM_PROMPT}\n\n{prompt_with_mem}\n\nINPUT TEXT:\n{text[:50000]}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt_text}]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        res_data = response.json()
        try:
            raw = res_data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise ValueError(f"Unexpected response structure from Gemini API: {res_data}")
        parsed = _extract_json(raw)
        return parse_loose_menu_extraction(parsed)

    payload = {
        "model": model or TEXT_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": f"{prompt_with_mem}\n\nINPUT TEXT:\n{text[:50000]}",
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
            "num_predict": 1000,
        },
    }
    raw = _post_generate(payload)
    parsed = _extract_json(raw)
    return parse_loose_menu_extraction(parsed)


def extract_from_image_with_ollama(image_path: str | Path, model: Optional[str] = None, api_key: Optional[str] = None, bypass_to_gemini: bool = True) -> MenuExtraction:
    image_bytes = Path(image_path).read_bytes()
    encoded = base64.b64encode(image_bytes).decode("utf-8")

    active_key = (api_key or GEMINI_API_KEY) if bypass_to_gemini else None
    prompt_with_mem = _get_prompt_with_memory()
    if active_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={active_key}"
        prompt_text = f"{SYSTEM_PROMPT}\n\n{prompt_with_mem}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt_text},
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": encoded
                        }
                    }
                ]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        res_data = response.json()
        try:
            raw = res_data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise ValueError(f"Unexpected response structure from Gemini API: {res_data}")
        parsed = _extract_json(raw)
        return parse_loose_menu_extraction(parsed)

    payload = {
        "model": model or VISION_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt_with_mem,
        "images": [encoded],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
            "num_predict": 1000,
        },
    }
    raw = _post_generate(payload)
    parsed = _extract_json(raw)
    return parse_loose_menu_extraction(parsed)


def extract_from_text_with_gemini(text: str, api_key: Optional[str] = None) -> MenuExtraction:
    active_key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not active_key:
        raise ValueError("GEMINI_API_KEY is not set in the environment or .env file.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={active_key}"
    prompt_with_mem = _get_prompt_with_memory()
    prompt_text = f"{SYSTEM_PROMPT}\n\n{prompt_with_mem}\n\nINPUT TEXT:\n{text}"
    
    gemini_schema = GEMINI_MENU_SCHEMA

    payload = {
        "contents": [{
            "parts": [{"text": prompt_text}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema
        }
    }
    
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    res_data = response.json()
    try:
        raw = res_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected response structure from Gemini API: {res_data}")
        
    parsed = _extract_json(raw)
    return parse_loose_menu_extraction(parsed)


def extract_from_images_with_gemini(image_paths: List[str | Path], api_key: Optional[str] = None) -> MenuExtraction:
    active_key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not active_key:
        raise ValueError("GEMINI_API_KEY is not set in the environment or .env file.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={active_key}"
    prompt_with_mem = _get_prompt_with_memory()
    prompt_text = f"{SYSTEM_PROMPT}\n\n{prompt_with_mem}"
    
    parts = [{"text": prompt_text}]
    for path in image_paths:
        image_bytes = Path(path).read_bytes()
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": encoded
            }
        })
        
    gemini_schema = GEMINI_MENU_SCHEMA

    payload = {
        "contents": [{
            "parts": parts
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema
        }
    }
    
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    res_data = response.json()
    try:
        raw = res_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected response structure from Gemini API: {res_data}")
        
    parsed = _extract_json(raw)
    return parse_loose_menu_extraction(parsed)


def merge_extractions(extractions: List[MenuExtraction]) -> MenuExtraction:
    all_items = []
    notes = []
    currency = "INR"
    seen = set()

    for ext in extractions:
        currency = ext.currency or currency
        notes.extend(ext.document_notes or [])
        for item in ext.items:
            # Deduplicate only exact same category/name/price combo across PDF page overlaps.
            price_key = "#".join(str(v.price) for v in item.variations)
            key = (item.category.lower().strip(), item.product_name.lower().strip(), price_key)
            if key not in seen:
                seen.add(key)
                all_items.append(item)

    return MenuExtraction(currency=currency, items=all_items, document_notes=notes)

