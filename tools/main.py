import base64
import json
import os
from google.cloud import pubsub_v1

# Assume critic_agent is packaged in your Cloud Function directory
from critic_agent import CriticAgent

def process_user_correction(event, context):
    """
    Triggered from a message on 'user-corrections-topic'.
    Acts as the Consumer, executes AI Logic, and Acts as a Publisher.
    """
    print("🧐 [CloudFunction] Triggered CriticAgent for human correction analysis.")
    
    pubsub_message = base64.b64decode(event['data']).decode('utf-8')
    payload = json.loads(pubsub_message)
    
    api_key = payload.get("api_key")
    
    # 1. Initialize and run the Agent
    agent = CriticAgent(api_key=api_key)
    try:
        proposed_rule = agent.analyze_correction(
            context_data=payload.get("context_data"),
            ai_decision=payload.get("ai_decision"),
            human_correction=payload.get("human_correction")
        )
        
        # Defensive check before publishing
        if not proposed_rule or not proposed_rule.get('title'):
            print(f"⚠️ [CloudFunction] Generated rule is invalid or empty. Aborting publish. Rule: {proposed_rule}")
            return

        # 2. Publish the new rule to the second topic so Django can receive it
        publisher = pubsub_v1.PublisherClient()
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        topic_path = publisher.topic_path(project_id, "draft-rules-topic")
        
        publisher.publish(topic_path, data=json.dumps(proposed_rule).encode("utf-8"))
        print(f"✅ [CloudFunction] Successfully drafted rule and published to draft-rules-topic: {proposed_rule.get('title')}")
    except Exception as e:
        print(f"⚠️ [CloudFunction] Failed to generate rule: {e}")