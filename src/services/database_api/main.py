from fastapi import FastAPI
from contextlib import asynccontextmanager
from core.database import init_pool, close_pool
from routers import preferences

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()       # startup
    yield
    await close_pool()      # shutdown

app = FastAPI(title="AutomotiveOS API", lifespan=lifespan)

app.include_router(preferences.router)

@app.get("/health")
async def health():
    return {"status": "ok"}