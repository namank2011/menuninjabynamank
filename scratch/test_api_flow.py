import os
import requests
import json
from pathlib import Path

# Load dev env variables
from dotenv import load_dotenv
load_dotenv()

API_URL = "http://127.0.0.1:8000/api/drafts"
IMAGE_FILE = Path(r"c:\Users\Amonex\QuickMenuAgent\uploads\aedb656387_WhatsApp Image 2026-07-27 at 1.48.13 PM.jpeg")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

def run_test():
    if not IMAGE_FILE.exists():
        print(f"Error: Sample image not found at {IMAGE_FILE}")
        return

    # Use a requests session to persist cookies
    session = requests.Session()

    # Log in first
    print("Logging in to obtain session token...")
    login_url = "http://127.0.0.1:8000/api/auth/login"
    login_res = session.post(login_url, json={
        "email": "namankshetri2@gmail.com",
        "password": "2011@Naman"
    })
    if login_res.status_code != 200:
        print(f"Authentication failed (status code: {login_res.status_code}): {login_res.text}")
        return
    print("Authenticated successfully.")
    
    session_token = session.cookies.get("session_token") or login_res.cookies.get("session_token")
    cookie_header = {"Cookie": f"session_token={session_token}"} if session_token else {}

    print(f"Sending e2e extract request for '{IMAGE_FILE.name}' -> {API_URL}")
    
    headers = {
        "X-Gemini-API-Key": GEMINI_KEY,
        **cookie_header
    }
    
    files = [
        ("menu_files", (IMAGE_FILE.name, open(IMAGE_FILE, "rb"), "image/jpeg"))
    ]
    
    data = {
        "business_name": "Test Render Bistro",
        "default_template": "true",
        "extraction_engine": "gemini",
        "tax_category": "Services",
        "tax_type": "GST",
        "tax_value": "5.0",
        "master_status": "Active",
        "menu_status": "Active",
        "stock_status": "Active",
        "station": "Kitchen",
        "preparation_time": "15 mins",
        "default_dietary": "",
        "direct_approve": "false"
    }

    try:
        r = session.post(API_URL, headers=headers, files=files, data=data, timeout=120)
        print(f"Response Code: {r.status_code}")
        response_json = r.json()
        print("Response JSON:")
        print(json.dumps(response_json, indent=2))
        
        draft_id = response_json.get("draftId")
        if draft_id:
            print(f"\nDraft successfully created with id: {draft_id}")
            print("Retrieving verified details...")
            
            r_details = session.get(f"{API_URL}/{draft_id}", headers=headers, timeout=30)
            details = r_details.json()
            items = details.get("items", [])
            print(f"Extracted product count: {len(items)}")
            for idx, it in enumerate(items[:5]):
                print(f"[{idx+1}] {it.get('productName')} - Category: {it.get('categoryName')} - Prices: {it.get('variations')}")
        else:
            print("Failed: Draft ID was not returned in response.")
            
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    run_test()
