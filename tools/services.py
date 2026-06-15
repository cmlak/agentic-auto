import os
from django.db.models import Q
from pgvector.django import CosineDistance
from google import genai
from google.genai import types
from account.models import AgentKnowledgeRule

def build_targeted_agent_prompt(raw_text, agent_type='GLOBAL', top_k=3):
    """
    RAG Implementation: Pulls atomic rules based on Vector Cosine-Similarity.
    Falls back to naive keyword matching if vector search fails.
    """
    base_query = Q(agent_scope=agent_type) | Q(agent_scope='GLOBAL')
    
    query_embedding = None
    api_key = os.getenv("GEMINI_API_KEY_2") or os.getenv("GEMINI_API_KEY")
    
    # 1. Generate the Embedding for the incoming query text
    if api_key and raw_text and raw_text.strip():
        try:
            client = genai.Client(api_key=api_key)
            # Truncate text to ensure we don't hit the embedding model's token limits
            text_to_embed = raw_text[:8000]
            try:
                embed_res = client.models.embed_content(
                    model='gemini-embedding-2',
                    contents=text_to_embed,
                    config=types.EmbedContentConfig(output_dimensionality=768)
                )
            except Exception as e:
                if '404' in str(e):
                    embed_res = client.models.embed_content(
                        model='gemini-embedding-001',
                        contents=text_to_embed,
                        config=types.EmbedContentConfig(output_dimensionality=768)
                    )
                else:
                    raise e
            if embed_res.embeddings:
                query_embedding = embed_res.embeddings[0].values
        except Exception as e:
            print(f"⚠️ Vector Embedding failed: {e}")

    targeted_rules_text = ""
    rule_count = 1

    if query_embedding:
        # 2a. Phase 4: True Cosine Similarity Vector RAG
        # Filter for rules that have embeddings and order by nearest (smallest Cosine Distance)
        active_rules = AgentKnowledgeRule.objects.filter(
            base_query, 
            is_active=True,
            embedding__isnull=False
        ).order_by(CosineDistance('embedding', query_embedding))[:top_k]
        
        for rule in active_rules:
            targeted_rules_text += f"RULE {rule_count}: {rule.title}\n"
            targeted_rules_text += f"WHEN: {rule.condition}\n"
            targeted_rules_text += f"ACTION: {rule.action_or_fact}\n\n"
            rule_count += 1
    else:
        # 2b. Phase 1 Fallback: Naive Keyword RAG
        active_rules = AgentKnowledgeRule.objects.filter(base_query, is_active=True)
        text_lower = raw_text.lower()
        for rule in active_rules:
            if not rule.tags:
                continue
            rule_tags = [tag.strip().lower() for tag in rule.tags.split(',')]
            if any(tag in text_lower for tag in rule_tags):
                targeted_rules_text += f"RULE {rule_count}: {rule.title}\n"
                targeted_rules_text += f"WHEN: {rule.condition}\n"
                targeted_rules_text += f"ACTION: {rule.action_or_fact}\n\n"
                rule_count += 1

    return targeted_rules_text if targeted_rules_text else "No specific overriding rules triggered. Use standard accounting logic."