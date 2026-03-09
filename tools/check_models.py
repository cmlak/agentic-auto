import os
from google import genai
from dotenv import load_dotenv

# 1. Load Environment
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY_2")

if not api_key:
    print("❌ Error: GEMINI_API_KEY not found in .env")
    exit()

print(f"🔑 Testing Key: {api_key[:5]}...{api_key[-4:]}")

try:
    # 2. Initialize Client
    client = genai.Client(api_key=api_key)
    
    print("\nAttempting to list available models...")
    print("-" * 30)
    
    # 3. List Models
    count = 0
    for m in client.models.list():
        # filter for models that support generating content
        if "generateContent" in m.supported_actions:
            print(f"✅ Found: {m.name}")
            count += 1
            
    if count == 0:
        print("\n❌ No models found. This usually means the API Key is invalid")
        print("   or the Google AI Studio project does not have the API enabled.")
    else:
        print("-" * 30)
        print(f"\n✨ Success! Found {count} usable models.")
        print("Use one of the names listed above exactly as it appears (excluding 'models/').")

except Exception as e:
    print(f"\n❌ CRITICAL ERROR: {e}")