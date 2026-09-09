from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import get_conn
from .routes import accounts, dashboard, tags, transactions, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_conn()  # bootstrap DB
    yield


app = FastAPI(title="Expense Tracker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")
app.include_router(transactions.router, prefix="/api")
app.include_router(tags.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")


@app.get("/")
def root():
    return {"ok": True, "service": "expense-tracker"}
