import os
from google import genai
from dotenv import load_dotenv
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Checks and lists available Google GenAI models.'

    def handle(self, *args, **options):
        # 1. Load Environment
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY_2")

        if not api_key:
            self.stdout.write(self.style.ERROR("❌ Error: GEMINI_API_KEY_2 not found in .env"))
            return

        self.stdout.write(f"🔑 Testing Key: {api_key[:5]}...{api_key[-4:]}")

        try:
            # 2. Initialize Client
            client = genai.Client(api_key=api_key)
            
            self.stdout.write("\nAttempting to list available models...")
            self.stdout.write("-" * 30)
            
            # 3. List Models
            count = 0
            for m in client.models.list():
                # filter for models that support generating content
                if "generateContent" in m.supported_actions:
                    self.stdout.write(self.style.SUCCESS(f"✅ Found: {m.name}"))
                    count += 1
                    
            if count == 0:
                self.stdout.write(self.style.ERROR("\n❌ No models found. This usually means the API Key is invalid or the Google AI Studio project does not have the API enabled."))
            else:
                self.stdout.write("-" * 30)
                self.stdout.write(self.style.SUCCESS(f"\n✨ Success! Found {count} usable models."))
                self.stdout.write("Use one of the names listed above exactly as it appears (excluding 'models/').")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n❌ CRITICAL ERROR: {e}"))