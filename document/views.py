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

# ==============================================================================
# 1. ENHANCED PYDANTIC SCHEMAS (For Strict Gemini Output)
# ==============================================================================
class ExtractedRule(BaseModel):
    agent_scope: Literal['GLOBAL', 'TAX', 'RECON', 'ECON'] = Field(
        description="Which agent should know this?"
    )
    rule_type: Literal['ACCOUNT_MAPPING', 'TAX_LAW', 'MACRO_FACT', 'ANTI_PATTERN', 'DOCUMENT_PARSING', 'WORKFLOW_ROUTING'] = Field(
        description="The strict classification of this rule."
    )
    title: str = Field(description="A concise, descriptive rule title.")
    condition: str = Field(description="WHEN does this rule apply? Be highly specific.")
    action_or_fact: str = Field(description="WHAT should the AI do or know?")
    tags: str = Field(description="Comma-separated keywords for vector search fallback.")
    priority_weight: int = Field(
        description="Priority. Use 10 for standard rules. Use 50-100 for strict exceptions or legal overrides."
    )

class RuleBatch(BaseModel):
    rules: List[ExtractedRule]


# ==============================================================================
# 2. UPLOAD & EXTRACTION VIEW (The Librarian Agent)
# ==============================================================================
@login_required
def upload_financial_report_view(request):
    if request.method == 'POST':
        form = FinancialReportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            
            # 1. Save the initial Source Document
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
                
                # 2. Extract raw text using Vertex AI Document AI
                project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
                location = os.getenv("DOCUMENTAI_LOCATION", "us")
                processor_id = os.getenv("DOCUMENTAI_PROCESSOR_ID")
                
                parsed_text = ""
                if project_id and processor_id:
                    docai_client = documentai.DocumentProcessorServiceClient()
                    name = docai_client.processor_path(project_id, location, processor_id)
                    raw_document = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
                    request_docai = documentai.ProcessRequest(name=name, raw_document=raw_document)
                    result = docai_client.process_document(request=request_docai)
                    parsed_text = result.document.text
                else:
                    parsed_text = "Fallback: Direct text extraction required. (DOCUMENTAI_PROCESSOR_ID missing)"

                # 3. Use Gemini 1.5 Pro to extract structured atomic rules
                prompt = f"""You are an elite Cambodian Macroeconomist and Tax Auditor. 
                Read the perfectly parsed text below and extract actionable rules, macroeconomic shifts, and compliance changes.
                
                CRITICAL INSTRUCTIONS:
                1. rule_type: You MUST classify the rule into one of the allowed categories. Do NOT use MACRO_FACT for procedural formatting or tax laws.
                2. priority_weight: Assign a weight of 10 for general facts/rules. If the rule explicitly states an EXCEPTION or a strict legal override, assign a weight between 50 and 100.
                3. ATOMICITY: Do not combine multiple distinct rules into one. If the text covers 3 different scenarios, output 3 separate JSON objects.

                Example Schema format:
                [{{ 
                    'agent_scope': 'TAX', 
                    'rule_type': 'TAX_LAW',
                    'title': 'WHT Exemption on Software', 
                    'condition': 'When a vendor provides cloud software services under Prakas 123', 
                    'action_or_fact': 'Do not deduct 15% WHT. Exempt the transaction.', 
                    'tags': 'software, WHT, exemption, prakas 123',
                    'priority_weight': 80
                }}]
                
                <PARSED_TEXT>
                {parsed_text}
                </PARSED_TEXT>"""
                
                response = client.models.generate_content(
                    model='gemini-1.5-pro',
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RuleBatch,
                        temperature=0.1  # Low temperature for strict analytical extraction
                    )
                )

                extracted_data = response.parsed
                
                # 4. Save the drafted rules for Human review
                if extracted_data and extracted_data.rules:
                    for rule in extracted_data.rules:
                        DraftKnowledgeRule.objects.create(
                            source_document=source_doc,
                            proposed_agent_scope=rule.agent_scope,
                            proposed_rule_type=rule.rule_type,
                            proposed_title=rule.title,
                            proposed_condition=rule.condition,
                            proposed_action_or_fact=rule.action_or_fact,
                            proposed_tags=rule.tags,
                            proposed_priority_weight=rule.priority_weight,
                            status='PENDING'
                        )
                    
                    source_doc.is_processed = True
                    source_doc.save()
                    messages.success(request, f"Successfully extracted {len(extracted_data.rules)} atomic rules.")
                else:
                    messages.warning(request, "No rules were extracted from the document.")
                    
                return redirect('document:review_draft_rules')

            except Exception as e:
                messages.error(request, f"Failed to extract information: {str(e)}")
                return redirect('document:upload_financial_report')
    else:
        form = FinancialReportUploadForm()

    return render(request, 'document/upload_financial_report.html', {'form': form})


# ==============================================================================
# 3. REVIEW & APPROVAL VIEW (Human-in-the-Loop)
# ==============================================================================
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
                    
                    # Handle Deletions/Rejections
                    if form.cleaned_data.get('DELETE'):
                        rule = form.instance
                        rule.status = 'REJECTED'
                        rule.save()
                        continue
                        
                    # Handle Approvals
                    if form.has_changed() or form.cleaned_data.get('status') == 'APPROVED':
                        rule = form.save(commit=False)
                        
                        # Only promote to Live Database if Approved and not already promoted
                        if rule.status == 'APPROVED' and not rule.promoted_to:
                            
                            # 1. Generate embeddings for Vector RAG search
                            embedding_val = None
                            if client:
                                content_to_embed = f"Title: {rule.proposed_title}\nCondition: {rule.proposed_condition}\nAction/Fact: {rule.proposed_action_or_fact}\nTags: {rule.proposed_tags}"
                                try:
                                    try:
                                        embed_res = client.models.embed_content(
                                            model='text-embedding-004', # Updated to latest standard embedding model
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

                            # 2. Create the live AgentKnowledgeRule using the dynamic extracted data
                            live_rule = AgentKnowledgeRule.objects.create(
                                agent_scope=rule.proposed_agent_scope,
                                rule_type=rule.proposed_rule_type,                 # Dynamically mapped from AI
                                priority_weight=rule.proposed_priority_weight,     # Dynamically mapped from AI
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