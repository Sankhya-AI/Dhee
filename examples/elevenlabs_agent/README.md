# Dhee + ElevenLabs Voice Agent

Dhee does not replace your ElevenLabs agent.

Dhee improves an ElevenLabs agent through:
1. dynamic variable memory context
2. Dhee server tool
3. post-call webhook checkpointing
4. optional remote MCP later

First usable flow:

```text
call start      -> Dhee returns {{dhee_context}}
during call     -> ElevenLabs calls the Dhee server tool
call end        -> ElevenLabs post-call webhook checkpoints into Dhee
next call       -> Dhee injects better context again
```

Start the sidecar:

```bash
pip install -e ".[api]"
export DHEE_HTTP_TOKEN="dev-token"
export ELEVENLABS_WEBHOOK_SECRET="..."
dhee serve --host 0.0.0.0 --port 8765 --profile elevenlabs
```

Unsigned post-call webhooks are rejected by default. For local development only, you can run:

```bash
dhee serve --port 8765 --profile elevenlabs --allow-unsigned-webhooks
```

Print the ElevenLabs configuration:

```bash
dhee elevenlabs init --public-url https://memory.example.com
```

At call start, POST to Dhee:

```http
POST /v1/runs/start
Authorization: Bearer dev-token
```

```json
{
  "user_id": "user_123",
  "app_id": "elevenlabs:support-agent",
  "task": "voice support call",
  "channel": "voice"
}
```

Pass `dynamic_variables` from the response into ElevenLabs, including `dhee_app_id`.

For the native Dhee 7.2.0 SDK bridge:

```python
from dhee import ElevenLabsAgent

agent = ElevenLabsAgent(
    public_base_url="https://memory.example.com",
    user_id="user_123",
    agent_id="support-agent",
)

conversation = agent.create_conversation(api_key="...")
conversation.start_session()
```

If you already have a custom ElevenLabs client, audio interface, or callbacks,
pass them into `create_conversation(...)`; Dhee wraps the callbacks to capture
useful memory events and leaves your agent flow in place.

For the smallest patch to an existing `Conversation(...)` constructor, use:

```python
memory = ElevenLabsAgent(
    public_base_url="https://memory.example.com",
    user_id="user_123",
    agent_id=agent_id,
)

conversation = Conversation(
    client=elevenlabs,
    agent_id=agent_id,
    config=memory.conversation_initiation_data(),
    client_tools=memory.client_tools(existing_client_tools),
    callback_user_transcript=memory.wrap_user_transcript(on_user_transcript),
    callback_agent_response=memory.wrap_agent_response(on_agent_response),
    audio_interface=audio_interface,
)
```
