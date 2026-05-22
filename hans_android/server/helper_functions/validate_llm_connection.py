# check_models.py
import os
import requests
import json
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(override=True)

API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

if not API_URL or not API_KEY:
    raise ValueError("API_URL or API_KEY not set in .env")

CUSTOM_API_URL = f"{API_URL.rstrip('/')}/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# ============================================================================
# STEP 1: Check available models
# ============================================================================
print("=" * 70)
print("STEP 1: Fetching available models")
print("=" * 70)

try:
    models_url = f"{API_URL.rstrip('/')}/v1/models"
    
    print(f"🔍 GET {models_url}\n")
    response = requests.get(models_url, headers=headers, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        models = data.get("data", [])
        
        print(f"✅ Found {len(models)} models:\n")
        for model in models:
            print(f"  • {model['id']}")
        
    else:
        print(f"❌ Error: {response.status_code}")
        print(f"Response: {response.text}")
        exit(1)
        
except Exception as e:
    print(f"❌ Connection Error: {e}")
    exit(1)

# ============================================================================
# STEP 2: Test chat completion
# ============================================================================
print("\n" + "=" * 70)
print(f"STEP 2: Testing chat completion with {LLM_MODEL}")
print("=" * 70)

chat_url = f"{API_URL.rstrip('/')}/v1/chat/completions"

payload = {
    "model": LLM_MODEL,
    "messages": [
        {"role": "user", "content": "Hello, how are you?"}
    ],
    "max_tokens": 512,
    "stream": False
}

try:
    print(f"\n🔍 POST {chat_url}")
    print(f"📦 Payload: {json.dumps(payload, indent=2)}\n")
    
    response = requests.post(chat_url, headers=headers, json=payload, timeout=30)
    
    if response.status_code == 200:
        data = response.json()
        
        print("✅ Response received:\n")
        print(json.dumps(data, indent=2))
        
        # Extract and display the message
        if "choices" in data and len(data["choices"]) > 0:
            message = data["choices"][0].get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls")
            
            print("\n" + "=" * 70)
            print("PARSED RESPONSE")
            print("=" * 70)
            print(f"\n📝 Assistant: {content}")
            
            if tool_calls:
                print(f"\n🔧 Tool calls detected: {len(tool_calls)}")
                for tool in tool_calls:
                    print(f"  • {tool.get('function', {}).get('name')}")
            else:
                print("\n🔧 No tool calls in response")
        
    else:
        print(f"❌ Error: {response.status_code}")
        print(f"Response: {response.text}")
        exit(1)
        
except Exception as e:
    print(f"❌ Connection Error: {e}")
    exit(1)

print("\n" + "=" * 70)
print("✅ All tests passed!")
print("=" * 70)