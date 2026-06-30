from .event_bus import EventBus
from .econ_agent import EconAgent
from .critic_agent import CriticAgent

def setup_agent_listeners():
    """Registers pure AI agents to listen to system events."""
    EventBus.subscribe("CURRENCY_RATES_UPDATED", handle_currency_update)
    EventBus.subscribe("USER_CORRECTION_LOGGED", handle_user_correction)
    print("[Listeners] AI Agent listeners successfully registered on EventBus.")

def handle_currency_update(payload: dict):
    """Consumer: Listens for currency updates, runs AI logic, and broadcasts result."""
    current_rate = payload.get("current_rate")
    average_last_month = payload.get("average_last_month")
    api_key = payload.get("api_key")
    
    print(f"[EconAgent] Received CURRENCY_RATES_UPDATED ({current_rate} vs {average_last_month}). Analyzing...")

    agent = EconAgent(api_key=api_key)
    result = agent.evaluate_currency_risk(current_rate, average_last_month)
    
    if result.get("is_risk"):
        # AI has determined a risk! Publish a new instruction event.
        EventBus.publish("SYSTEM_NOTIFICATION_REQUIRED", {
            "agent_type": "ECON",
            "severity": "INFO" if result['deviation_pct'] < 0.5 else "WARNING",
            "title": "Daily Currency Analysis" if result['deviation_pct'] < 0.5 else "Currency Volatility Risk Detected",
            "message": f"The NBC official exchange rate has deviated to {current_rate} KHR/USD (a {result['deviation_pct']:.2f}% change).\n\nAI Analysis: {result['analysis']}",
            "action_url": ""
        })

def handle_user_correction(payload: dict):
    """Consumer: Listens for human corrections and triggers CriticAgent for self-healing."""
    api_key = payload.get("api_key")
    context = payload.get("context_data")
    ai_decision = payload.get("ai_decision")
    human_correction = payload.get("human_correction")
    
    print(f"[CriticAgent] Analyzing human correction: '{ai_decision}' -> '{human_correction}'")
    
    agent = CriticAgent(api_key=api_key)
    try:
        agent_response = agent.analyze_correction(context, ai_decision, human_correction)
        
        if agent_response.status == 'FAILURE':
            print(f"[CriticAgent] Failed to generate rule. Reason: {agent_response.error_message}")
            return
            
        proposed_rule = agent_response.payload
        
        if not proposed_rule or not proposed_rule.get('title'):
            print(f"[CriticAgent] Agent returned SUCCESS but rule is invalid. Rule: {proposed_rule}")
            return
            
        EventBus.publish("DRAFT_RULE_PROPOSED", proposed_rule)
    except Exception as e:
        print(f"CRITICAL [CriticAgent] Infrastructure error during critic execution: {e}")