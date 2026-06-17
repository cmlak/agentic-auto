import base64
import json
import os

from main import process_user_correction

# 1. Mock Environment Variables
os.environ["GEMINI_API_KEY"] = "your_actual_api_key_here"  # Replace with your key
os.environ["GOOGLE_CLOUD_PROJECT"] = "document-project-464509"

# 2. Mock Pub/Sub Payload
payload = {
    "context_data": "Vendor: Test Vendor, Description: Office Supplies",
    "ai_decision": "Mapped to Account: 100000",
    "human_correction": "Changed to Account: 725080"
}

# 3. Encode the payload exactly as Cloud Pub/Sub does (Base64)
payload_bytes = json.dumps(payload).encode("utf-8")
b64_data = base64.b64encode(payload_bytes).decode('utf-8')

mock_event = {
    "data": b64_data
}

mock_context = {}  # Context is rarely used in simple data extraction, an empty dict is fine

# 4. Execute the function locally
print("🚀 Running process_user_correction locally...")
process_user_correction(mock_event, mock_context)