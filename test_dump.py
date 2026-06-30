import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'agentic_platform.settings')
django.setup()

from account.management.commands.run_doc_agent import Command
cmd = Command()
cmd.handle(pdf_dir="C:\\bakertilly\\BakerTilly\\CCKT\\02. Client's Info\\Antigravity")
