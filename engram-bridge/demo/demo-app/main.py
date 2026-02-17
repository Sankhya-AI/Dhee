"""Demo app — a simple user registration API with no validation."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="User Service")

# In-memory user store
users: dict[str, dict] = {}


class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str
    age: int


class UpdateProfileRequest(BaseModel):
    bio: str
    website: str


@app.post("/users")
def create_user(req: CreateUserRequest):
    """Create a new user account."""
    users[req.username] = {
        "username": req.username,
        "email": req.email,
        "password": req.password,  # stored in plaintext!
        "age": req.age,
        "bio": "",
        "website": "",
    }
    return {"ok": True, "username": req.username}


@app.get("/users/{username}")
def get_user(username: str):
    """Get user profile."""
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Leaks password in response!
    return user


@app.put("/users/{username}/profile")
def update_profile(username: str, req: UpdateProfileRequest):
    """Update user profile."""
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user["bio"] = req.bio
    user["website"] = req.website
    return user


@app.delete("/users/{username}")
def delete_user(username: str):
    """Delete a user."""
    if username in users:
        del users[username]
        return {"ok": True}
    raise HTTPException(status_code=404, detail="User not found")
