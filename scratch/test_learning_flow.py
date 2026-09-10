import os
import requests
import json
import sqlite3
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_URL = "http://127.0.0.1:8000"

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from database import get_db_connection

def test_learning_flow():
    session = requests.Session()
    
    # 1. Login
    print("Logging in...")
    login_res = session.post(f"{API_URL}/api/auth/login", json={
        "email": "namankshetri2@gmail.com",
        "password": "2011@Naman"
    })
    if login_res.status_code != 200:
        print("Login failed")
        return
        
    session_token = login_res.cookies.get("session_token")
    headers = {"X-Session-Token": session_token}
    if session_token:
        headers["Cookie"] = f"session_token={session_token}"

    # 2. Get drafts
    print("Fetching drafts...")
    drafts_res = session.get(f"{API_URL}/api/drafts", headers=headers)
    drafts = drafts_res.json()
    if not drafts:
        print("No drafts found")
        return
    
    draft_id = drafts[0]["id"]
    print(f"Using draft: {draft_id}")
    
    # Fetch details
    details_res = session.get(f"{API_URL}/api/drafts/{draft_id}", headers=headers)
    draft = details_res.json()
    
    if not draft.get("items"):
        print("Draft has no items")
        return
        
    # We will pick the first item to edit/correct
    item = draft["items"][0]
    initial_product_name = item["source"].get("initialProductName") or "Test Item"
    item["source"]["initialProductName"] = initial_product_name
    
    # Setup test corrections
    corrected_product_name = initial_product_name + " Corrected"
    corrected_category = "Beverages Specials"
    corrected_dietary = "Veg"
    
    print(f"Applying corrections onto item {item['id']}:")
    print(f"- Initial Name: {initial_product_name} -> Corrected: {corrected_product_name}")
    print(f"- Category: {item['categoryName']} -> Corrected: {corrected_category}")
    print(f"- Dietary Tag: {item['dietaryTag']} -> Corrected: {corrected_dietary}")
    
    item["productName"] = corrected_product_name
    item["categoryName"] = corrected_category
    item["dietaryTag"] = corrected_dietary
    item["approved"] = True
    
    # Send draft update
    update_res = session.put(
        f"{API_URL}/api/drafts/{draft_id}",
        headers=headers,
        json={
            "businessName": draft["businessName"],
            "defaults": draft["defaults"],
            "items": draft["items"]
        }
    )
    print(f"Update status: {update_res.status_code}")
    assert update_res.status_code == 200
    
    # Clean up learning memory before approval to isolate test results
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM learning_memory WHERE entry_type IN ('product_name', 'product_category', 'product_dietary')")
    db.commit()
    db.close()
    
    # Now approve the draft
    print("Approving draft to trigger learning flow...")
    approve_res = session.post(
        f"{API_URL}/api/drafts/{draft_id}/approve",
        headers=headers,
        json={"approvedAgreement": True}
    )
    print(f"Approve response: {approve_res.status_code}")
    assert approve_res.status_code == 200
    
    # Query database to check if corrections are recorded
    print("\nReading learning_memory table from database...")
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("SELECT entry_type, original_val, corrected_val, frequency FROM learning_memory")
    records = cursor.fetchall()
    
    learning_db_data = {}
    for rec in records:
        print(f'- Saved Memory: Type={rec[0]}, Original="{rec[1]}", Corrected="{rec[2]}", Frequency={rec[3]}')
        learning_db_data[(rec[0], rec[1])] = rec[2]
        
    db.close()
    
    # Assertions
    # 1. Product name spelling correction
    name_key = ( 'product_name', initial_product_name )
    assert name_key in learning_db_data, f"Spelling correction key {name_key} not saved"
    assert learning_db_data[name_key] == corrected_product_name
    
    # 2. Product-to-category mapping
    cat_key = ( 'product_category', corrected_product_name )
    assert cat_key in learning_db_data, f"Product-to-category mapping {cat_key} not saved"
    assert learning_db_data[cat_key] == corrected_category
    
    # 3. Product-to-dietary mapping
    diet_key = ( 'product_dietary', corrected_product_name )
    assert diet_key in learning_db_data, f"Product-to-dietary mapping {diet_key} not saved"
    assert learning_db_data[diet_key] == corrected_dietary
    
    print("\nFeedback loop database checking succeeded! All assertions passed!")

if __name__ == "__main__":
    test_learning_flow()
