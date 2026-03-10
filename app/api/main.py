from fastapi import FastAPI
from app.api.v1.routers import auth, ticket_classifier, categories, subcategories, utils


app = FastAPI()


app.include_router(auth.router)
app.include_router(ticket_classifier.router)
app.include_router(categories.router)
app.include_router(subcategories.router)
app.include_router(utils.router)

@app.get("/health")
async def health_check():
    return {"status": "ok"}