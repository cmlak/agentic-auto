import threading
from typing import Any, Dict, Optional, Literal
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

class AgentResponse(BaseModel):
    status: Literal['SUCCESS', 'FAILURE']
    payload: Optional[Any] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class BaseAutonomousAgent:
    """
    A framework-agnostic base class for all autonomous agents.
    Handles LLM connectivity, token tracking, cost calculations, and network retries.
    """
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-pro"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.cost_lock = threading.Lock()
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}
        
        # Standardized rates (Updated for Gemini 2.5)
        self.model_rates = {
            "gemini-3.1-pro-preview": {"in": 1.25, "out": 10.00},
            "gemini-2.5-pro": {"in": 1.25, "out": 5.00},
            "gemini-2.5-flash": {"in": 0.075, "out": 0.30}
        }

    def calculate_cost(self, usage) -> float:
        rates = self.model_rates.get(self.model_name, {"in": 0.10, "out": 0.40})
        if usage:
            return ((usage.prompt_token_count / 1e6) * rates["in"]) + ((usage.candidates_token_count / 1e6) * rates["out"])
        return 0.0

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=3, max=30), reraise=True)
    def execute_task(self, contents: list, response_schema: Any = None, temperature: float = 0.0) -> AgentResponse:
        """Core execution loop with built-in telemetry and exponential backoff."""
        config_kwargs = {"temperature": temperature}
        if response_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema
            
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs)
            )
            
            cost = self.calculate_cost(response.usage_metadata)
            with self.cost_lock:
                target_bucket = "flash_cost" if "flash" in self.model_name.lower() else "pro_cost"
                self.cost_stats[target_bucket] += cost
                
            parsed_payload = response.parsed if response_schema else response.text
            if not parsed_payload:
                return AgentResponse(
                    status='FAILURE',
                    error_message="LLM returned an empty or unparseable response.",
                    metadata={'cost': cost}
                )

            return AgentResponse(
                status='SUCCESS',
                payload=parsed_payload,
                metadata={'cost': cost}
            )
        except Exception as e:
            return AgentResponse(
                status='FAILURE',
                error_message=f"LLM API call failed: {str(e)}",
                metadata={'cost': 0.0}
            )