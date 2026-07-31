import requests
import json

API_URL = "http://127.0.0.1:8000"

def test_user_flow():
    # Start requests session
    session = requests.Session()
    
    # 1. Login as Super Admin
    print("Logging in as Super Admin...")
    admin_login_res = session.post(f"{API_URL}/api/auth/login", json={
        "email": "namankshetri2@gmail.com",
        "password": "2011@Naman"
    })
    print(f"Super Admin Login Response: {admin_login_res.status_code}")
    if admin_login_res.status_code != 200:
        print("Failed to login as admin")
        return
        
    admin_token = admin_login_res.cookies.get("session_token")
    admin_headers = {}
    if admin_token:
        admin_headers["Cookie"] = f"session_token={admin_token}"
        admin_headers["X-Session-Token"] = admin_token
        
    # 2. Delete test user if already exists to ensure fresh registration
    print("Deleting test user if exists...")
    del_res = session.delete(f"{API_URL}/api/users/test_operator@company.com", headers=admin_headers)
    print(f"Delete user output: {del_res.status_code}")

    # 3. Create a standard operator user
    print("Registering new Menu Operator 'test_operator@company.com'...")
    create_res = session.post(f"{API_URL}/api/users", headers=admin_headers, json={
        "email": "test_operator@company.com",
        "role": "user",
        "password": "TestPassword123"
    })
    print(f"Create User Response Code: {create_res.status_code}")
    print("Create User JSON:")
    print(json.dumps(create_res.json(), indent=2))
    
    if create_res.status_code != 200:
        return
        
    # 4. Login as the newly created operator user
    print("\nLogging in as 'test_operator@company.com'...")
    user_session = requests.Session()
    user_login_res = user_session.post(f"{API_URL}/api/auth/login", json={
        "email": "test_operator@company.com",
        "password": "TestPassword123"
    })
    print(f"Operator Login Response Code: {user_login_res.status_code}")
    print("Operator Login JSON:")
    print(json.dumps(user_login_res.json(), indent=2))
    
    if user_login_res.status_code != 200:
        return
        
    user_token = user_login_res.cookies.get("session_token")
    user_headers = {}
    if user_token:
        user_headers["Cookie"] = f"session_token={user_token}"
        user_headers["X-Session-Token"] = user_token
        
    # 5. Get current profile to check 'me' endpoint
    me_res = user_session.get(f"{API_URL}/api/auth/me", headers=user_headers)
    print(f"Retrieve Me Response Code: {me_res.status_code}")
    print("Me profile JSON:")
    print(json.dumps(me_res.json(), indent=2))
    
    # 6. List drafts (should be empty for new user since they haven't uploaded anything)
    list_res = user_session.get(f"{API_URL}/api/drafts", headers=user_headers)
    print(f"List drafts for Operator (should be empty): {list_res.status_code}")
    print(f"Drafts: {list_res.json()}")
    
    # 7. Try to access users list endpoint (should be blocked - HTTP 403)
    users_res = user_session.get(f"{API_URL}/api/users", headers=user_headers)
    print(f"Operator accessing Admin users list: {users_res.status_code} (Expected 403)")
    print(f"Admin endpoint response: {users_res.json()}")

    # 8. Clean up by deleting the user
    print("\nCleaning up user...")
    clean_res = session.delete(f"{API_URL}/api/users/test_operator@company.com", headers=admin_headers)
    print(f"Cleanup status: {clean_res.status_code}")

if __name__ == "__main__":
    test_user_flow()
