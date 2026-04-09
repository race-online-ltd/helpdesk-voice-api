from fastapi import FastAPI
from app.api.v1.routers import auth, ticket_classifier, categories, subcategories, utils


app = FastAPI()


app.include_router(auth.router, prefix="/api/v1")
app.include_router(ticket_classifier.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(subcategories.router, prefix="/api/v1")
app.include_router(utils.router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    return {"status": "ok"}