import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
print(f"API Key: {api_key[:10] if api_key else 'None'}...")

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
payload = {
    "contents": [{
        "parts": [{"text": "Hello, respond with key validation message: 'Gemini Key is Valid!'"}]
    }]
}
headers = {"Content-Type": "application/json"}
try:
    response = requests.post(url, json=payload, headers=headers, timeout=20)
    print("Status Code:", response.status_code)
    print("Response JSON:", response.json())
except Exception as e:
    print("Error:", e)
