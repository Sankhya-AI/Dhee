import json
from typing import Optional

from dhee.llms.base import BaseLLM


class MockLLM(BaseLLM):
    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)

    def generate(self, prompt: str) -> str:
        lowered = prompt.lower()
        if "memories" in lowered and "consolidated_memory" in lowered:
            response = {
                "consolidated_memory": "",
                "preserved_facts": [],
                "discarded_as_redundant": [],
                "confidence": 0.0,
            }
            return json.dumps(response)
        if "classification" in lowered and "subsumes" in lowered:
            response = {
                "classification": "COMPATIBLE",
                "confidence": 0.5,
                "merged_content": None,
                "explanation": "mock response",
            }
            return json.dumps(response)
        if "memories" in lowered and "importance" in lowered:
            response = {
                "memories": [],
                "reasoning": "mock response",
            }
            return json.dumps(response)
        return ""
