import os
import requests
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_URL = "http://127.0.0.1:8000"
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

def run_desc_test():
    session = requests.Session()
    
    # 1. Login
    print("Logging in...")
    login_res = session.post(f"{API_URL}/api/auth/login", json={
        "email": "namankshetri2@gmail.com",
        "password": "2011@Naman"
    })
    if login_res.status_code != 200:
        print("Login failed format")
        return
        
    session_token = login_res.cookies.get("session_token")
    headers = {"X-Gemini-API-Key": GEMINI_KEY}
    if session_token:
        headers["Cookie"] = f"session_token={session_token}"
        
    # 2. Get list of drafts
    drafts_res = session.get(f"{API_URL}/api/drafts", headers=headers)
    drafts = drafts_res.json()
    if not drafts or not isinstance(drafts, list):
        print("No drafts found to test")
        return
        
    draft_id = drafts[0]["id"]
    print(f"Testing description generation on draft: {draft_id}")
    
    # Get all items in draft
    det_res = session.get(f"{API_URL}/api/drafts/{draft_id}", headers=headers)
    draft_details = det_res.json()
    items = draft_details.get("items", [])
    if not items:
        print("No items in draft")
        return
        
    item_ids = [it["id"] for it in items]
    print(f"Draft has {len(items)} items. Triggering bulk AI description for items...")
    
    # 3. Call generate-descriptions with overwrite=True
    desc_payload = {
        "itemIds": item_ids,
        "overwrite": True
    }
    desc_res = session.post(
        f"{API_URL}/api/drafts/{draft_id}/generate-descriptions",
        headers=headers,
        json=desc_payload
    )
    print(f"Generate Response code: {desc_res.status_code}")
    print("Response JSON:")
    print(json.dumps(desc_res.json(), indent=2))
    
    # Wait, check if descriptions got updated
    det_res = session.get(f"{API_URL}/api/drafts/{draft_id}", headers=headers)
    draft_details2 = det_res.json()
    items2 = draft_details2.get("items", [])
    
    updated_items = [it for it in items2 if it.get("description")]
    print(f"Number of items that now have descriptions: {len(updated_items)}")
    for it in updated_items[:3]:
        print(f"- {it['productName']}: {it['description']}")
        
if __name__ == "__main__":
    run_desc_test()
