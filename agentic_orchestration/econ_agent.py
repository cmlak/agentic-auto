from .base_agent import BaseAutonomousAgent

class EconAgent(BaseAutonomousAgent):
    """
    Specialized agent for macroeconomic analysis.
    Decoupled from Django ORM to run anywhere (FastAPI, CLI, Serverless).
    """
    
    def evaluate_currency_risk(self, current_rate: float, average_last_month: float) -> dict:
        """Pure AI logic for evaluating economic risk. No DB dependencies."""
        deviation_pct = 0.0
        if average_last_month > 0:
            deviation_pct = abs(current_rate - average_last_month) / average_last_month * 100
            
        if deviation_pct < 0.5:
            return {"is_risk": False, "deviation_pct": deviation_pct, "analysis": ""}
            
        prompt = (
            f"Act as an expert corporate currency risk analyst. "
            f"The current NBC official exchange rate is {current_rate} KHR/USD, compared to the 30-day "
            f"average of {average_last_month:.2f} KHR/USD. Provide a concise, 2-3 sentence analysis "
            f"of this fluctuation and a brief actionable recommendation for corporate cash management."
        )
        
        # Inherited execute_task handles LLM connectivity and token counting
        analysis = self.execute_task(contents=[prompt])
        
        return {"is_risk": True, "deviation_pct": deviation_pct, "analysis": analysis.strip()}