from fastapi import FastAPI
from app.api.v1.routers import ticket_classifier


app = FastAPI()


app.include_router(ticket_classifier.router)

@app.get("/health")
async def health_check():
    return {"status": "ok"}