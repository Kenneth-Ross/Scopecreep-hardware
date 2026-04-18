from fastapi import FastAPI
from api.routes.psu import router as psu_router

app = FastAPI(title="JBHack Hardware Backend")

app.include_router(psu_router)

@app.get("/health")
def health():
    return {"status": "ok"}
