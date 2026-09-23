"""
Token + lead server for the BizApp247 avatar widget.

Endpoints:
  POST /api/token   -> mints a LiveKit access token for a fresh demo room
  POST /api/lead    -> forwards the submitted form to a GHL inbound webhook
                       and returns the redirect URL for the demo page

Run:
  uvicorn main:app --host 0.0.0.0 --port 8080
"""

import os
import time
import uuid
from datetime import timedelta

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel, EmailStr, Field

load_dotenv()

app = FastAPI(title="BizApp247 Avatar Demo API")

app.add_middleware(
    CORSMiddleware,
    # Lock this to the WordPress domain(s) in production.
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["POST"],
    allow_headers=["*"],
)

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
GHL_WEBHOOK_URL = os.environ["GHL_INBOUND_WEBHOOK_URL"]
DEMO_PAGE_URL = os.environ.get("DEMO_PAGE_URL", "https://bizapp247.com/demo")

# Naive in-memory rate limit so bots can't burn avatar minutes.
# Replace with Redis if you run more than one instance.
_recent: dict[str, float] = {}
RATE_WINDOW_S = 60


class TokenRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)


class Lead(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=25)
    business_name: str = Field(min_length=1, max_length=200)
    industry: str = Field(min_length=1, max_length=100)


@app.post("/api/token")
async def create_token(req: TokenRequest):
    now = time.time()
    last = _recent.get(req.client_id, 0)
    if now - last < RATE_WINDOW_S:
        raise HTTPException(429, "Please wait a moment before starting a new session.")
    _recent[req.client_id] = now

    room_name = f"bizapp-demo-{uuid.uuid4().hex[:12]}"
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(f"visitor-{req.client_id[:16]}")
        .with_name("Visitor")
        .with_ttl(timedelta(minutes=20))  # 20-minute demo cap
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name="aria"
                    )
                ],
            ),
        ).to_jwt()
    )
    return {"url": LIVEKIT_URL, "token": token, "room": room_name}


@app.post("/api/lead")
async def submit_lead(lead: Lead):
    payload = {
        **lead.model_dump(),
        "source": "bizapp247_avatar_demo",
        # GHL workflow keys off this tag: Wait 5 min -> Retell outbound call
        "tags": ["avatar-demo-lead", f"industry-{lead.industry.lower().replace(' ', '-')}"],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(GHL_WEBHOOK_URL, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(502, "Lead delivery failed — please try again.")

    return {"redirect_url": f"{DEMO_PAGE_URL}?industry={lead.industry.lower().replace(' ', '-')}"}
