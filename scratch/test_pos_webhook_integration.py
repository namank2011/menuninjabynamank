import os
import requests
import json
import time
import sys
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

API_URL = "http://127.0.0.1:8000"
MOCK_WEBHOOK_PORT = 8899
MOCK_WEBHOOK_URL = f"http://127.0.0.1:{MOCK_WEBHOOK_PORT}/webhook"

# Global store for captured webhooks
received_webhooks = []

class MockWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode('utf-8'))
            received_webhooks.append(data)
        except Exception as e:
            print(f"[Mock Webhook Server] Error parsing JSON: {e}")
            received_webhooks.append({"raw": body.decode('utf-8')})
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "captured"}')

    def log_message(self, format, *args):
        # Prevent spamming the stdout during tests
        return

def run_mock_webhook_server():
    server = HTTPServer(('127.0.0.1', MOCK_WEBHOOK_PORT), MockWebhookHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[Mock Webhook Server] Running on port {MOCK_WEBHOOK_PORT}...")
    return server

def run_integration_tests():
    # 1. Start mock webhook server
    server = run_mock_webhook_server()

    # 2. Login as Super Admin to manage POS Companies
    session = requests.Session()
    print("[Test] Logging in as Super Admin...")
    login_res = session.post(f"{API_URL}/api/auth/login", json={
        "email": "namankshetri2@gmail.com",
        "password": "2011@Naman"
    })
    
    if login_res.status_code != 200:
        print("[FAIL] Admin login failed. Make sure backend is running.")
        return
        
    admin_token = login_res.cookies.get("session_token")
    headers = {"X-Session-Token": admin_token}
    if admin_token:
        headers["Cookie"] = f"session_token={admin_token}"

    print("[Test] Logged in successfully.")

    # 3. Clean up any existing POS test integrations if they exist
    print("[Test] Loading active POS integrations...")
    list_res = session.get(f"{API_URL}/api/admin/pos-companies", headers=headers)
    if list_res.status_code == 200:
        companies = list_res.json()
        for c in companies:
            if c["company_name"] == "Integration Test POS":
                print(f"[Test] Cleaning up existing integration {c['id']}")
                session.delete(f"{API_URL}/api/admin/pos-companies/{c['id']}", headers=headers)

    # 4. Register a new POS Integration Partner
    print("[Test] Registering a new POS partner: 'Integration Test POS'...")
    reg_payload = {
        "company_name": "Integration Test POS",
        "output_format": "slickpos",
        "webhook_url": MOCK_WEBHOOK_URL,
        "api_key": "test-pos-integration-api-key-xyz"
    }
    create_res = session.post(f"{API_URL}/api/admin/pos-companies", json=reg_payload, headers=headers)
    assert create_res.status_code == 200, f"Registration failed: {create_res.text}"
    created_pos = create_res.json()["pos_company"]
    print(f"[Test] POS Partner registered: ID={created_pos['id']}, Key={created_pos['api_key']}")

    # 5. Verify it appears in the active partner list
    print("[Test] Verifying active POS partner list...")
    list_res = session.get(f"{API_URL}/api/admin/pos-companies", headers=headers)
    assert list_res.status_code == 200
    partners = list_res.json()
    assert any(p["company_name"] == "Integration Test POS" for p in partners), "Registered partner not found in list"

    # 6. Test high-level POS menu extraction API using heuristics engine (offline)
    # We will upload a mock menu file. Let's create a temporary txt file.
    temp_menu = Path("temp_menu_test.txt")
    temp_menu.write_text("Cheese Pizza - 299\nVegetable Burger - 149\nCoca Cola - 49", encoding="utf-8")

    print("[Test] POSTing menu to programmatic /api/v1/pos/extract...")
    with open(temp_menu, "rb") as f:
        files = {"menu_files": (temp_menu.name, f, "text/plain")}
        data = {
            "business_name": "Integration Test Cafe",
            "extraction_engine": "heuristics",
            "direct_approve": "true",
            "tax_category": "Goods",
            "tax_type": "GST",
            "tax_value": "5.0",
            "master_status": "Active",
            "default_dietary": "veg",
            "station": "Kitchen"
        }
        pos_headers = {
            "X-POS-API-Key": "test-pos-integration-api-key-xyz"
        }
        res = requests.post(f"{API_URL}/api/v1/pos/extract", data=data, files=files, headers=pos_headers)

    # Clean up temp file
    if temp_menu.exists():
        temp_menu.unlink()

    print(f"[Test] POS Extraction HTTP Code: {res.status_code}")
    assert res.status_code == 200, f"Extraction failed: {res.text}"
    res_data = res.json()
    assert res_data.get("direct_approved") is True, f"Response doesn't indicate direct approval matching: {res.text}"
    assert "draftId" in res_data
    print(f"[Test] Direct approve extraction successful. Draft ID: {res_data['draftId']}")

    # 7. Check if webhook was received by the mock server callback!
    print("[Test] Waiting for async webhook dispatcher task to execute...")
    time.sleep(3) # Wait 3 seconds for async thread trigger

    print(f"[Test] Checking received webhooks on mock server. Count={len(received_webhooks)}")
    assert len(received_webhooks) > 0, "Webhook was never received by mock webhook delivery server!"
    
    webhook_payload = received_webhooks[0]
    print("[Test] Captured Webhook Payload Contents:")
    print(json.dumps(webhook_payload, indent=2))

    assert webhook_payload.get("event") == "menu.approved"
    assert webhook_payload.get("business_name") == "Integration Test Cafe"
    assert webhook_payload.get("output_format") == "slickpos"
    assert len(webhook_payload.get("items", [])) > 0
    print("[Test] Webhook payload assertions passed!")

    # 8. Clean up POS Integration Company
    print(f"[Test] Deleting test POS integration...")
    del_res = session.delete(f"{API_URL}/api/admin/pos-companies/{created_pos['id']}", headers=headers)
    assert del_res.status_code == 200
    print("[SUCCESS] All Webhook and Integration API test steps succeeded!")

    server.shutdown()

if __name__ == "__main__":
    run_integration_tests()
