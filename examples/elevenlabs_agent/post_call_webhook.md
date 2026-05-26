# Post-Call Webhook

Configure ElevenLabs post-call transcription webhooks to:

```text
POST https://memory.example.com/v1/webhooks/elevenlabs/post_call
```

Dhee will:

```text
reject unsigned webhooks unless local development explicitly allows them
verify signature when ELEVENLABS_WEBHOOK_SECRET is set
extract user_id and dhee_run_id from dynamic variables
normalize transcript turns into voice events
admit only durable memories
checkpoint the final call summary
```

The webhook endpoint returns `200` for ignored event types so webhook delivery does not flap during setup.
