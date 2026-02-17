"""Basic tests for the user service — no validation tests yet."""

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_create_user():
    resp = client.post("/users", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "secret123",
        "age": 28,
    })
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"


def test_get_user():
    client.post("/users", json={
        "username": "bob",
        "email": "bob@test.com",
        "password": "pass",
        "age": 30,
    })
    resp = client.get("/users/bob")
    assert resp.status_code == 200
    assert resp.json()["email"] == "bob@test.com"


def test_get_user_not_found():
    resp = client.get("/users/nobody")
    assert resp.status_code == 404


def test_delete_user():
    client.post("/users", json={
        "username": "charlie",
        "email": "c@test.com",
        "password": "pw",
        "age": 25,
    })
    resp = client.delete("/users/charlie")
    assert resp.status_code == 200
