import base64
import json
import os
import functions_framework
from google.cloud import pubsub_v1

# Ensure correct package import for your environment
from agentic_orchestration.critic_agent import CriticAgent

@functions_framework.cloud_event
def process_user_correction(cloud_event):
    """
    Triggered from a message on 'user-corrections-topic'.
    Acts as the Consumer, executes AI Logic, and Acts as a Publisher.
    
    Compatible with 2nd Gen Cloud Functions and Functions Framework.
    """
    print("🧐 [CloudFunction] Triggered CriticAgent for human correction analysis.")
    
    # 1. Environment Variable Check
    api_key = os.getenv("GEMINI_API_KEY_2") or os.getenv("GEMINI_API_KEY")
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    
    if not api_key:
        print("CRITICAL [CloudFunction] API Key not set.")
        return
    if not project_id:
        print("CRITICAL [CloudFunction] GOOGLE_CLOUD_PROJECT not set.")
        return

    # 2. Extract and Decode Data (2nd Gen / CloudEvent Format)
    try:
        # Access nested data from CloudEvent
        message = cloud_event.data.get("message", {})
        pubsub_data = message.get("data")
        attributes = message.get("attributes", {})

        if not pubsub_data or not attributes:
            print("⚠️ [CloudFunction] Received event with no message data.")
            return

        schema_name_from_attr = attributes.get('tenant_schema', 'cckt')
        decoded_message = base64.b64decode(pubsub_data).decode('utf-8')
        payload = json.loads(decoded_message)
    except Exception as e:
        print(f"CRITICAL [CloudFunction] Failed to decode Pub/Sub payload: {e}")
        return
    
    # 3. Initialize and run the CriticAgent
    agent = CriticAgent(api_key=api_key)
    try:
        agent_response = agent.analyze_correction(
            context_data=payload.get("context_data"),
            ai_decision=payload.get("ai_decision"),
            human_correction=payload.get("human_correction")
        )
        
        if agent_response.status == 'FAILURE':
            print(f"⚠️ [CloudFunction] CriticAgent failed. Reason: {agent_response.error_message}")
            return

        proposed_rule = agent_response.payload

        if not proposed_rule or not proposed_rule.get('title'):
            print(f"⚠️ [CloudFunction] Invalid rule content. Aborting. Data: {proposed_rule}")
            return

        # 4. Publish result back to Topic 2 (Loop-back to Django)
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(project_id, "draft-rules-topic")
        
        # Ensure draft_id is passed through if it exists in original payload
        proposed_rule['draft_id'] = payload.get('draft_id')
        proposed_rule['schema_name'] = payload.get('schema_name', schema_name_from_attr)
        
        # Tag the published message with the tenant_schema attribute
        tenant_schema = str(proposed_rule['schema_name'])
        
        publisher.publish(topic_path, data=json.dumps(proposed_rule).encode("utf-8"), tenant_schema=tenant_schema)
        print(f"✅ [CloudFunction] Published rule to draft-rules-topic: {proposed_rule.get('title')}")

    except Exception as e:
        print(f"CRITICAL [CloudFunction] Infrastructure error: {e}")
        # Re-raise so Pub/Sub knows the delivery attempt failed
        raise e
