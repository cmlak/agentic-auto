import os
from django.db import models
from django.utils import timezone

def generate_upload_path(instance, filename):
    # 1. Get the extension and base name
    ext = filename.split('.')[-1]
    name = ".".join(filename.split('.')[:-1]) # Handles names like "my.test.file.pdf"
    
    # 2. Get the current time in Asia/Bangkok (based on settings.TIME_ZONE)
    local_now = timezone.localtime(timezone.now())
    
    # 3. Format the timestamp: YearMonthDay-HourMinuteSecond
    # Example: 20260305-173005 (5:30 PM Bangkok time)
    timestamp_str = local_now.strftime("%Y%m%d-%H%M%S")
    date_folder = local_now.strftime("%Y%m%d")
    
    # 4. Create the new filename
    new_filename = f"{name}_{timestamp_str}.{ext}"
    
    # 5. Save into a subfolder named by the date
    return os.path.join('uploads', date_folder, new_filename)

class Document(models.Model):
    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=255)
    upload = models.FileField(upload_to=generate_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title