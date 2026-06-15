from typing import Literal
from pydantic import BaseModel, Field
from .base_agent import BaseAutonomousAgent

class ProposedRule(BaseModel):
    agent_scope: Literal['GLOBAL', 'TAX', 'RECON', 'ECON'] = Field(
        default='GLOBAL', description="The agent scope. Default to GLOBAL if unsure."
    )
    title: str = Field(description="Short, descriptive rule title.")
    condition: str = Field(description="When does this rule apply? Be specific based on the failure context.")
    action_or_fact: str = Field(description="What should the AI do or know? Provide exact, actionable instructions.")
    tags: str = Field(description="Comma separated tags for vector metadata (e.g., 'wht, vendor, error_correction')")

class CriticAgent(BaseAutonomousAgent):
    """
    Reflective agent that analyzes AI mapping failures and autonomously 
    proposes new AgentKnowledgeRules to prevent future mistakes.
    """
    def analyze_correction(self, context_data: str, ai_decision: str, human_correction: str) -> dict:
        prompt = f"""
        You are an elite AI Alignment Critic. Your job is to analyze a mistake made by another AI agent 
        and propose a new, permanent Knowledge Rule to prevent this mistake in the future.
        
        <CONTEXT_DOCUMENT_DATA>
        {context_data}
        </CONTEXT_DOCUMENT_DATA>
        
        <AI_ORIGINAL_DECISION>
        {ai_decision}
        </AI_ORIGINAL_DECISION>
        
        <HUMAN_CORRECTION_APPLIED>
        {human_correction}
        </HUMAN_CORRECTION_APPLIED>
        
        Analyze the discrepancy. Why did the AI fail? 
        Output a structured JSON object proposing a new RAG rule to guide the AI next time. 
        Focus on the underlying accounting principle or mapping logic, not just the specific document.
        """
        
        result = self.execute_task(contents=[prompt], response_schema=ProposedRule)
        
        if not result:
            raise ValueError("CriticAgent failed to generate a valid rule from the LLM response. The response was empty or could not be parsed.")
            
        return result.model_dump()