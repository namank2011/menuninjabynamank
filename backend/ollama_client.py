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
You are a strict, high-fidelity restaurant menu extraction engine for Menu Ninja POS bulk upload.
Return ONLY valid JSON matching the requested structure.
No explanations, markdown, or comments.
Never invent or hallucinate menu items or prices. Use ONLY visible input data.
""".strip()

MENU_EXTRACTION_PROMPT = """
Extract restaurant menu items into JSON structure:
{
  "currency": "INR",
  "document_notes": [],
  "items": [
    {
      "category": "category name",
      "product_name": "clean item name",
      "description": "short description",
      "dietary_tag": "veg" or "non veg" or "egg" or "",
      "confidence": 0.9,
      "source_text": "raw snippet",
      "variations": [{"name": "Regular", "price": 99.0, "listing_price": 120.0}]
    }
  ]
}

Strict Rules:
1. Extract ALL visible dishes and drinks. Never invent/extrapolate items.
2. Group items with different sizes/prices as one item with multiple variations (e.g. Small 99, Large 149).
3. If one price, set name="" and price. Do not round decimals.
4. Clean product_name: remove prices and category headers. Keep exact spelling.
5. Set dietary_tag to "veg", "non veg", "egg" or "" (infer logically: chicken is non-veg, paneer is veg, egg is egg).
6. Category defaults to "Uncategorized" if no nearby heading.
7. Ignore restaurant contact info, address, GST, licenses.
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
        guidelines.append("--- Corrected Naming Guidelines (Format matching items as below) ---")
        for orig, corr in list(prod_memory.items())[:5]:
            guidelines.append(f'- Formatting "{orig}" -> output: "{corr}"')
            
    if cat_memory:
        guidelines.append("--- Corrected Category Guidelines (Classify matching items as below) ---")
        for orig, corr in list(cat_memory.items())[:5]:
            guidelines.append(f'- Classify "{orig}" under category: "{corr}"')
            
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


def _post_to_gemini(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 60) -> requests.Response:
    import time
    import re
    
    # List of models to try in sequence on quota/rate limit/error failures
    model_rotation = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-8b", "gemini-1.5-pro"]
    
    # Extract API key from the url
    key_match = re.search(r"key=([^&]+)", url)
    api_key = key_match.group(1) if key_match else ""
    
    for current_model in model_rotation:
        target_url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={api_key}"
        
        max_retries = 2
        retry_delay = 1.0  # seconds
        
        for attempt in range(max_retries):
            try:
                print(f"Querying Gemini model '{current_model}' (Attempt {attempt+1}/{max_retries})...")
                response = requests.post(target_url, json=payload, headers=headers, timeout=timeout)
                
                # If Gemini returned rate limiting or server error Status Code
                if response.status_code in [400, 403, 404, 429, 500, 502, 503, 504]:
                    print(f"Gemini model '{current_model}' HTTP status {response.status_code}. Trying next model...")
                    break
                    
                response.raise_for_status()
                
                # Check for rate limiting or other api errors within successful payload
                res_data = response.json()
                if "error" in res_data:
                    error_code = res_data["error"].get("code")
                    print(f"Gemini API payload error: {res_data['error'].get('message')}. Trying next model...")
                    break
                    
                # Success!
                return response
                
            except requests.exceptions.RequestException as e:
                # If it's an HTTP Status error, try next model
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Gemini model '{current_model}' HTTP Error: {e}. Trying next model...")
                    break
                
                # If it's a network socket/connection error or timeout, retry with current model
                if attempt == max_retries - 1:
                    print(f"Transient failures with model '{current_model}' after retries. Trying next model...")
                    break
                    
                print(f"Transient error with '{current_model}': {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 1.5
                
    # Final fallback attempt with the original URL
    print(f"Attempting final query fallback with original url...")
    return requests.post(url, json=payload, headers=headers, timeout=timeout)


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
                "responseMimeType": "application/json",
                "temperature": 0.0
            }
        }
        headers = {"Content-Type": "application/json"}
        response = _post_to_gemini(url, payload, headers, timeout=60)
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
                "responseMimeType": "application/json",
                "temperature": 0.0
            }
        }
        headers = {"Content-Type": "application/json"}
        response = _post_to_gemini(url, payload, headers, timeout=60)
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
            "responseSchema": gemini_schema,
            "temperature": 0.0
        }
    }
    
    headers = {"Content-Type": "application/json"}
    response = _post_to_gemini(url, payload, headers, timeout=120)
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
            "responseSchema": gemini_schema,
            "temperature": 0.0
        }
    }
    
    headers = {"Content-Type": "application/json"}
    response = _post_to_gemini(url, payload, headers, timeout=120)
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

