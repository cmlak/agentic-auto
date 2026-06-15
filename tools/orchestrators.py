import re
import io
import difflib
import threading
from datetime import datetime
import json
from google.cloud import pubsub_v1
from pypdf import PdfReader
from django.utils import timezone

from .models import Vendor, Purchase
from account.models import Account, AgentNotification

# Import the decoupled agents from Phase 1
try:
    from agentic_orchestration.invoice_agent import InvoiceAgent
    from tools.services import build_targeted_agent_prompt
    from agentic_orchestration.econ_agent import EconAgent
    from agentic_orchestration.event_bus import EventBus
    from agentic_orchestration.listeners import setup_agent_listeners
except ImportError:
    pass

class InvoiceOrchestrator:
    """
    Coordinates between the Django DB (Memory) and the AI Invoice Agent (Brain).
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.agent = InvoiceAgent(api_key=api_key)
        self.vendor_lock = threading.Lock()
        self.batch_new_vendors = {}

    def _get_coa_context(self) -> str:
        """Retrieves Chart of Accounts from DB and converts to string context."""
        coa_qs = Account.objects.all().order_by('account_id')
        if not coa_qs.exists():
            return "No Chart of Accounts provided."
        return "\n".join([f"{a.account_id} - {a.name} ({a.account_type})" for a in coa_qs])

    def resolve_and_assign_vendor(self, raw_name, vattin, vat_amount):
        """Django-specific logic to match extracted vendor names against the DB."""
        general_vendor, _ = Vendor.objects.get_or_create(
            vendor_id='V-00001', defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )

        if not raw_name or str(raw_name).strip().lower() in ['unknown', 'n/a', 'none', '']:
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        exact_match = Vendor.objects.filter(normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

        best_vendor, best_coverage = None, 0.0
        for v in Vendor.objects.all():
            if not v.normalized_name or not target_norm: continue
            ratio = difflib.SequenceMatcher(None, target_norm, v.normalized_name).ratio()
            containment_score = 0.85 if (f" {target_norm} " in f" {v.normalized_name} " or f" {v.normalized_name} " in f" {target_norm} ") else 0.0
            score = max(ratio, containment_score)
            if score >= 0.75 and score > best_coverage:
                best_coverage = score
                best_vendor = v

        if best_vendor:
            return {'db_id': best_vendor.id, 'is_new': False, 'temp_vid': None}

        with self.vendor_lock:
            if target_norm in self.batch_new_vendors:
                return self.batch_new_vendors[target_norm]

            all_vids = Vendor.objects.all().values_list('vendor_id', flat=True)
            max_num = max([int(re.search(r'V-?(\d+)', str(vid)).group(1)) for vid in all_vids if re.search(r'V-?(\d+)', str(vid))] + [1])
            
            current_seq = max_num + 1 + len(self.batch_new_vendors)
            new_vid = f"V-{current_seq:05d}"
            vendor_data = {'db_id': None, 'is_new': True, 'temp_vid': new_vid, 'temp_id': f"TEMP_{new_vid}"}
            self.batch_new_vendors[target_norm] = vendor_data
            return vendor_data

    def process_single_page(self, pdf_bytes: bytes, pg: int, custom_prompt: str="", batch_name: str="", rules_context: str="", memo_context: str="", current_invoice_seq: int=1, date_prefix: str="", is_explicit_seq: bool=False):
        """
        The Core Pipeline:
        1. Get DB State -> 2. Pass to AI Agent -> 3. Post-process AI response into Django schema.
        """
        print(f"\n🚀 [InvoiceOrchestrator] Initiating Agentic Workflow for Page {pg}...")
        coa_context = self._get_coa_context()
        
        # Phase 4: Generate True Vector RAG Context
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            raw_text = "\n".join([p.extract_text() or "" for p in reader.pages])
        except Exception:
            raw_text = ""
            
        rag_rules = build_targeted_agent_prompt(raw_text, agent_type='TAX')
        print(f"🔍 [Vector RAG] Semantic Rules successfully retrieved and bound to agent prompt.")

        try:
            # Call the decoupled Agent
            print(f"🧠 [InvoiceAgent] Analyzing document structure independent of Django...")
            raw_entries = self.agent.process_single_page(
                pdf_bytes=pdf_bytes, page_num=pg,
                coa_context=coa_context, rag_rules=rag_rules, custom_prompt=custom_prompt
            )
            
            # Retrieve costs safely
            with self.agent.cost_lock:
                page_cost = self.agent.cost_stats["pro_cost"] + self.agent.cost_stats["flash_cost"]
                
            ledgers = []
            is_split_invoice = len(raw_entries) > 1
            
            print(f"⚙️ [Orchestrator] AI extracted {len(raw_entries)} entries. Resolving Django Vendors...")
            # Determine Sequence and resolve Vendors (Django-side data mutation)
            for idx, entry_dict in enumerate(raw_entries, 1):
                if 'vendor_name' in entry_dict: 
                    entry_dict['company'] = entry_dict.pop('vendor_name')
                
                reasoning = entry_dict.pop('account_reasoning', '')
                entry_dict['instruction'] = f"AI Reason: {reasoning}" if reasoning else ""
                
                # Query the Django DB via the Orchestrator
                vendor_data = self.resolve_and_assign_vendor(
                    entry_dict.get('company', ''), entry_dict.get('vattin', ''), entry_dict.get('vat_usd', 0.0)
                )
                
                entry_dict['vendor_db_id'] = vendor_data.get('db_id')
                entry_dict['is_new_vendor'] = vendor_data.get('is_new', False)
                entry_dict['temp_vid'] = vendor_data.get('temp_vid')
                entry_dict['temp_id'] = vendor_data.get('temp_id')
                entry_dict['vendor_choice'] = vendor_data.get('temp_id') if vendor_data.get('is_new') else vendor_data.get('db_id')
                entry_dict['batch'] = batch_name
                
                ledgers.append(entry_dict)

            return ledgers, page_cost, current_invoice_seq, None
            
        except Exception as e:
            return [], 0.0, current_invoice_seq, str(e)

class SystemOrchestrator:
    """Coordinates non-invoice workflows like Macro-Economics and System Health."""
    
    @staticmethod
    def evaluate_and_broadcast_currency_risk(current_rate: float, average_last_month: float, api_key: str):
        """
        PHASE 3: Event-Driven trigger.
        Instead of waiting for the LLM and locking up the Django view, we just publish an event and return instantly.
        """
        EventBus.publish("CURRENCY_RATES_UPDATED", {
            "current_rate": current_rate,
            "average_last_month": average_last_month,
            "api_key": api_key
        })
        return True

    @staticmethod
    def submit_correction_feedback(context_data: str, ai_decision: str, human_correction: str, api_key: str):
        """
        PHASE 5: Autonomously triggers the CriticAgent when a human overrides the AI.
        """
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            print("⚠️ [SystemOrchestrator] GOOGLE_CLOUD_PROJECT missing. Cannot publish to Pub/Sub.")
            return False
            
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(project_id, "user-corrections-topic")
        
        payload = {
            "context_data": context_data,
            "ai_decision": ai_decision,
            "human_correction": human_correction,
            "api_key": api_key
        }
        
        data_bytes = json.dumps(payload).encode("utf-8")
        publisher.publish(topic_path, data=data_bytes)
        print(f"📡 [SystemOrchestrator] Published correction feedback to Pub/Sub.")
        return True

class DjangoEventOrchestrator:
    """Listens to AI output events and executes Django ORM writes safely."""
    
    @staticmethod
    def handle_system_notification(payload: dict):
        from clients.models import Client
        from django_tenants.utils import schema_context
        
        print(f"💾 [DjangoEventOrchestrator] Writing Notification to DB: {payload.get('title')}")
        for tenant in Client.objects.exclude(schema_name='public'):
            with schema_context(tenant.schema_name):
                AgentNotification.objects.create(
                    agent_type=payload.get('agent_type', 'SYSTEM'),
                    severity=payload.get('severity', 'INFO'),
                    title=payload.get('title'),
                    message=payload.get('message'),
                    action_url=payload.get('action_url', ''),
                    is_resolved=False
                )

    @staticmethod
    def handle_draft_rule_proposed(payload: dict):
        from document.models import DraftKnowledgeRule, SourceDocument
        
        print(f"📝 [DjangoEventOrchestrator] Autonomously drafting new rule: {payload.get('title')}")
        try:
            # Get or create a generic placeholder for autonomous system feedback safely
            source_doc = SourceDocument.objects.filter(title="Autonomous Critic Feedback").first()
            if not source_doc:
                source_doc = SourceDocument.objects.create(
                    title="Autonomous Critic Feedback",
                    source_url='System Generated',
                    date_issued=timezone.now().date(),
                    is_processed=True
                )
            
            DraftKnowledgeRule.objects.create(
                source_document=source_doc,
                proposed_agent_scope=payload.get('agent_scope', 'GLOBAL'),
                proposed_title=payload.get('title'),
                proposed_condition=payload.get('condition'),
                proposed_action_or_fact=payload.get('action_or_fact'),
                proposed_tags=payload.get('tags'),
                status='PENDING'
            )
        except Exception as e:
            print(f"⚠️ [DjangoEventOrchestrator] Failed to save Draft Rule: {e}")

# Initialize Pub/Sub Listeners on startup
try:
    setup_agent_listeners()
    EventBus.subscribe("SYSTEM_NOTIFICATION_REQUIRED", DjangoEventOrchestrator.handle_system_notification)
    EventBus.subscribe("DRAFT_RULE_PROPOSED", DjangoEventOrchestrator.handle_draft_rule_proposed)
except NameError:
    pass