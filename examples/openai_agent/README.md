# Dhee + OpenAI Responses API Agent

Dhee does not replace your OpenAI app. It adds memory context, a `dhee_memory` function tool, and session checkpoints.

Minimal patch to an existing Responses API call:

```python
from openai import OpenAI
from dhee import OpenAIAgent

client = OpenAI(api_key="...")
memory = OpenAIAgent(user_id="user_123", model="gpt-4.1")

response = client.responses.create(
    **memory.response_create_kwargs(
        input="What should you remember about me?",
    )
)
```

When the model returns function calls, execute them and send outputs back:

```python
tool_outputs = memory.function_call_outputs(response)

followup = client.responses.create(
    model=memory.model,
    input=tool_outputs,
    previous_response_id=response.id,
    tools=memory.tools(),
)
```

Convenience call:

```python
response = memory.create_response(
    "Please remember I prefer concise summaries.",
    client=client,
)
memory.finish("OpenAI session completed.")
```
