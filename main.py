import base64
import json
import os
from google.cloud import pubsub_v1

# The Cloud Function's source is now the project root.
# Update the import to point to the correct package 'agentic_orchestration'
from agentic_orchestration.critic_agent import CriticAgent

def process_user_correction(event, context):
    """
    Triggered from a message on 'user-corrections-topic'.
    Acts as the Consumer, executes AI Logic, and Acts as a Publisher.
    """
    print("🧐 [CloudFunction] Triggered CriticAgent for human correction analysis.")
    
    api_key = os.getenv("GEMINI_API_KEY_2") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("CRITICAL [CloudFunction] GEMINI_API_KEY_2 or GEMINI_API_KEY environment variable not set.")
        return

    pubsub_message = base64.b64decode(event['data']).decode('utf-8')
    payload = json.loads(pubsub_message)
    
    # 1. Initialize and run the Agent
    agent = CriticAgent(api_key=api_key)
    try:
        agent_response = agent.analyze_correction(
            context_data=payload.get("context_data"),
            ai_decision=payload.get("ai_decision"),
            human_correction=payload.get("human_correction")
        )
        
        if agent_response.status == 'FAILURE':
            print(f"⚠️ [CloudFunction] CriticAgent failed to generate a rule. Reason: {agent_response.error_message}")
            return

        proposed_rule = agent_response.payload

        if not proposed_rule or not proposed_rule.get('title'):
            print(f"⚠️ [CloudFunction] Agent returned SUCCESS but rule is invalid. Aborting publish. Rule: {proposed_rule}")
            return

        # 2. Publish the new rule to the second topic so Django can receive it
        publisher = pubsub_v1.PublisherClient()
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        topic_path = publisher.topic_path(project_id, "draft-rules-topic")
        
        publisher.publish(topic_path, data=json.dumps(proposed_rule).encode("utf-8"))
        print(f"✅ [CloudFunction] Successfully drafted rule and published to draft-rules-topic: {proposed_rule.get('title')}")
    except Exception as e: # This now catches infrastructure errors, not agent logic errors
        print(f"CRITICAL [CloudFunction] Infrastructure error during critic execution: {e}")