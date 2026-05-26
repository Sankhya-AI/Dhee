from __future__ import annotations

import os

import requests


DHEE_URL = os.getenv("DHEE_URL", "https://memory.example.com")
DHEE_TOKEN = os.getenv("DHEE_HTTP_TOKEN", "dev-token")


def start_call(user_id: str) -> dict:
    response = requests.post(
        f"{DHEE_URL.rstrip('/')}/v1/runs/start",
        json={
            "user_id": user_id,
            "app_id": "elevenlabs:support-agent",
            "task": "voice support call",
            "channel": "voice",
        },
        headers={"Authorization": f"Bearer {DHEE_TOKEN}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["dynamic_variables"]


if __name__ == "__main__":
    print(start_call("user_123"))
