from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from . import models  # noqa: F401  (registers tables on Base.metadata)
from .db import Base, engine
from .routers import hr, hr_auth, interview

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="HR Interview Scoring API", lifespan=lifespan)

api_router = APIRouter(prefix="/api/v1")


@api_router.get("/health")
def health():
    return {"status": "ok"}


app.include_router(api_router)
app.include_router(interview.router)
app.include_router(hr_auth.router)
app.include_router(hr.router)