from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routers import auth, ticket_classifier, categories, subcategories, utils


app = FastAPI(
    title="Auto Ticket Classifier API",
    description="Backend API for audio complaint ingestion, transcription, and helpdesk ticket classification.",
    version="0.1.0",
)

# CORS: allow all origins (use a restricted list in production).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router, prefix="/api/v1")
app.include_router(ticket_classifier.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(subcategories.router, prefix="/api/v1")
app.include_router(utils.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok"}