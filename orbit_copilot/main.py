"""
Orbit Copilot — FastAPI entry point.

Run:  uvicorn orbit_copilot.main:app --port 8001
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import chat
from .config import get_settings
from .db import init_copilot_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_copilot_db()
    yield


app = FastAPI(
    title="Orbit Copilot",
    description="Conversational, tool-using AI copilot for the Orbit personal finance app.",
    version=__version__,
    lifespan=lifespan,
)

origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # Browsers reject credentials with a wildcard origin.
    allow_credentials=origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)


@app.get("/")
async def root():
    return {"app": "Orbit Copilot", "status": "ready", "documentation": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}
