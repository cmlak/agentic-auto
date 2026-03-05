import os
from django.db import models
from django.utils import timezone

def generate_upload_path(instance, filename):
    # 1. Clean the filename
    # If the user uploads "test.pdf", ext = "pdf", base_name = "test"
    ext = filename.split('.')[-1]
    base_name = os.path.splitext(filename)[0]
    
    # 2. Get current time in Bangkok
    # timezone.localtime() uses the TIME_ZONE defined in settings.py (Asia/Bangkok)
    local_now = timezone.localtime(timezone.now())
    
    # 3. Formats
    # Folder format: 2026-03-05
    # Timestamp format: 173005
    date_folder = local_now.strftime("%Y%m%d")
    timestamp_str = local_now.strftime("%H%M%S")
    
    # 4. Create the final filename: name_date_time.ext
    # This prevents the "Double Year" issue
    new_filename = f"{base_name}_{date_folder}-{timestamp_str}.{ext}"
    
    # 5. Result: uploads/20260305/original_name_20260305-173005.pdf
    return os.path.join('uploads', date_folder, new_filename)

class Document(models.Model):
    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=255)
    upload = models.FileField(upload_to=generate_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title