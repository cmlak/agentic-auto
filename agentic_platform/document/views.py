from django.shortcuts import render
from django.http import HttpResponse
from .models import Document

def test_db_connection(request):
    # 1. Try to write to Cloud SQL
    new_doc = Document.objects.create(title="Test Document from Cloud Run")
    
    # 2. Try to read from Cloud SQL
    all_docs = Document.objects.all()
    
    output = f"Successfully saved: {new_doc.title} at {new_doc.uploaded_at}<br><br>"
    output += "<b>All entries in Database:</b><br>"
    for doc in all_docs:
        output += f"- {doc.title} ({doc.id})<br>"
        
    return HttpResponse(output)