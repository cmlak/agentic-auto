from django.db.models import Q
from account.models import AgentKnowledgeRule

def build_targeted_agent_prompt(raw_text, agent_type='GLOBAL'):
    """
    RAG Implementation: Pulls atomic rules based on keyword hits in the text.
    """
    # 1. Base query: Get rules for this specific agent (or global rules)
    base_query = Q(agent_scope=agent_type) | Q(agent_scope='GLOBAL')
    active_rules = AgentKnowledgeRule.objects.filter(base_query, is_active=True)

    targeted_rules_text = ""
    rule_count = 1

    # 2. Naive Keyword RAG: Check if the tags exist in the text
    text_lower = raw_text.lower()
    
    for rule in active_rules:
        rule_tags = [tag.strip().lower() for tag in rule.tags.split(',')]
        
        # If any tag is found in the text, inject this rule!
        if any(tag in text_lower for tag in rule_tags):
            targeted_rules_text += f"RULE {rule_count}: {rule.title}\n"
            targeted_rules_text += f"WHEN: {rule.condition}\n"
            targeted_rules_text += f"ACTION: {rule.action_or_fact}\n\n"
            rule_count += 1

    return targeted_rules_text if targeted_rules_text else "No specific overriding rules triggered. Use standard accounting logic."