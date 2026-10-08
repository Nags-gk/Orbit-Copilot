"""
Small helpers shared by the LLM providers.
"""


def clean_json_response(result_text: str) -> str:
    """Helper to strip markdown block wrap that local LLMs famously add to JSON responses."""
    text = result_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()
