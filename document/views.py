import os
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from pydantic import BaseModel, Field
from typing import List, Literal
from google import genai
from google.genai import types
from django.db import transaction
from google.cloud import documentai
from account.models import AgentKnowledgeRule

from .models import Document, SourceDocument, DraftKnowledgeRule
from .forms import FinancialReportUploadForm, DraftKnowledgeRuleFormSet

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

class ExtractedRule(BaseModel):
    agent_scope: Literal['GLOBAL', 'TAX', 'RECON', 'ECON'] = Field(description="The agent scope.")
    title: str = Field(description="Rule title")
    condition: str = Field(description="When does this rule apply?")
    action_or_fact: str = Field(description="What should the AI do or know?")
    tags: str = Field(description="Comma separated tags")

class RuleBatch(BaseModel):
    rules: List[ExtractedRule]

@login_required
def upload_financial_report_view(request):
    if request.method == 'POST':
        form = FinancialReportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            source_doc = SourceDocument.objects.create(
                title=form.cleaned_data['title'],
                source_url=form.cleaned_data['source_url'],
                document_pdf=form.cleaned_data['document_pdf'],
                date_issued=form.cleaned_data['date_issued'],
                is_processed=False
            )

            api_key = os.getenv("GEMINI_API_KEY_2")
            if not api_key:
                messages.error(request, "System Error: GEMINI_API_KEY_2 is missing.")
                return redirect('document:upload_financial_report')

            client = genai.Client(api_key=api_key)
            
            try:
                pdf_bytes = form.cleaned_data['document_pdf'].read()
                
                # 1. Use the Vertex AI Document Parsing API (Google Cloud Document AI)
                project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
                location = os.getenv("DOCUMENTAI_LOCATION", "us")
                processor_id = os.getenv("DOCUMENTAI_PROCESSOR_ID")
                
                parsed_text = ""
                if project_id and processor_id:
                    docai_client = documentai.DocumentProcessorServiceClient()
                    name = docai_client.processor_path(project_id, location, processor_id)
                    raw_document = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
                    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
                    result = docai_client.process_document(request=request)
                    parsed_text = result.document.text
                else:
                    parsed_text = "Fallback: Direct text extraction required. (DOCUMENTAI_PROCESSOR_ID missing)"

                # 2. Use Gemini 1.5 Pro for Extraction
                prompt = f"""You are an elite Cambodian Macroeconomist and Tax Auditor. Read the perfectly parsed text below.
                Extract actionable rules, macroeconomic shifts, and compliance changes.
                Output an array of JSON objects matching this schema:
                [{ 'agent_scope': 'ECON', 'title': 'Q2 Inflation Shift', 'condition': 'When assessing purchasing power or FX logic', 'action_or_fact': 'The inflation rate has risen to 3.2%. Adjust cash flow forecasting models accordingly.', 'tags': 'inflation, Q2, NBC' }]
                ONLY extract definitive facts and actionable directives. Ignore fluff.
                
                <PARSED_TEXT>
                {parsed_text}
                </PARSED_TEXT>"""
                
                response = client.models.generate_content(
                    model='gemini-1.5-pro',
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RuleBatch,
                        temperature=0.0
                    )
                )

                extracted_data = response.parsed
                
                if extracted_data and extracted_data.rules:
                    for rule in extracted_data.rules:
                        DraftKnowledgeRule.objects.create(
                            source_document=source_doc,
                            proposed_agent_scope=rule.agent_scope,
                            proposed_title=rule.title,
                            proposed_condition=rule.condition,
                            proposed_action_or_fact=rule.action_or_fact,
                            proposed_tags=rule.tags,
                            status='PENDING'
                        )
                    
                    source_doc.is_processed = True
                    source_doc.save()
                    messages.success(request, f"Successfully extracted {len(extracted_data.rules)} rules.")
                else:
                    messages.warning(request, "No rules were extracted from the document.")
                    
                return redirect('document:review_draft_rules')

            except Exception as e:
                messages.error(request, f"Failed to extract information: {str(e)}")
                return redirect('document:upload_financial_report')
    else:
        form = FinancialReportUploadForm()

    return render(request, 'document/upload_financial_report.html', {'form': form})

@login_required
def review_draft_rules_view(request):
    pending_rules = DraftKnowledgeRule.objects.filter(status='PENDING')
    
    if request.method == 'POST':
        formset = DraftKnowledgeRuleFormSet(request.POST, queryset=pending_rules)
        if formset.is_valid():
            instances_saved = 0
            
            api_key = os.getenv("GEMINI_API_KEY_2")
            client = genai.Client(api_key=api_key) if api_key else None
            
            with transaction.atomic():
                for form in formset:
                    if form.cleaned_data.get('DELETE'):
                        rule = form.instance
                        rule.status = 'REJECTED'
                        rule.save()
                        continue
                        
                    if form.has_changed() or form.cleaned_data.get('status') == 'APPROVED':
                        rule = form.save(commit=False)
                        if rule.status == 'APPROVED' and not rule.promoted_to:
                            
                            # Generate embeddings for RAG Pipeline ingestion
                            embedding_val = None
                            if client:
                                content_to_embed = f"Title: {rule.proposed_title}\nCondition: {rule.proposed_condition}\nAction/Fact: {rule.proposed_action_or_fact}\nTags: {rule.proposed_tags}"
                                try:
                                    try:
                                        embed_res = client.models.embed_content(
                                            model='gemini-embedding-2',
                                            contents=content_to_embed,
                                            config=types.EmbedContentConfig(output_dimensionality=768)
                                        )
                                    except Exception as e:
                                        if '404' in str(e):
                                            embed_res = client.models.embed_content(
                                                model='gemini-embedding-001',
                                                contents=content_to_embed,
                                                config=types.EmbedContentConfig(output_dimensionality=768)
                                            )
                                        else:
                                            raise e
                                    if embed_res.embeddings:
                                        embedding_val = embed_res.embeddings[0].values
                                except Exception as e:
                                    print(f"Embedding generation failed: {e}")

                            live_rule = AgentKnowledgeRule.objects.create(
                                agent_scope=rule.proposed_agent_scope,
                                rule_type='MACRO_FACT',
                                tags=rule.proposed_tags,
                                title=rule.proposed_title,
                                condition=rule.proposed_condition,
                                action_or_fact=rule.proposed_action_or_fact,
                                embedding=embedding_val
                            )
                            rule.promoted_to = live_rule
                        rule.save()
                        instances_saved += 1
                        
            messages.success(request, f"Successfully reviewed and updated {instances_saved} rules.")
            return redirect('document:review_draft_rules')
        else:
            messages.error(request, "Validation failed. Please correct the errors below.")
    else:
        formset = DraftKnowledgeRuleFormSet(queryset=pending_rules)
        
    return render(request, 'document/review_draft_rules.html', {'formset': formset})
