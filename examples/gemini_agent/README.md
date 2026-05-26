# Dhee + Gemini API Agent

Dhee does not replace your Gemini API app. It adds memory context, a `dhee_memory` function tool, and session checkpoints.

Minimal patch to an existing Gemini API call:

```python
from google import genai
from dhee import GeminiAgent

client = genai.Client(api_key="...")
memory = GeminiAgent(user_id="user_123", model="gemini-2.5-flash")

response = client.models.generate_content(
    model=memory.model,
    contents="What should you remember about me?",
    config=memory.generate_content_config(),
)
```

The Python Gemini SDK can automatically call Python functions passed in `tools`, so `generate_content_config()` includes `memory.dhee_memory` by default.

Manual function-calling mode:

```python
config = memory.generate_content_config(automatic_function_calling=False)
response = client.models.generate_content(
    model=memory.model,
    contents="Recall my follow-up preference.",
    config=config,
)

for call in response.function_calls:
    result = memory.handle_function_call(call)
```

Convenience call:

```python
response = memory.generate_content(
    "Please remember I prefer concise summaries.",
    client=client,
)
memory.finish("Gemini session completed.")
```
