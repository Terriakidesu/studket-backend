from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app import api, web

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(web.router)
app.include_router(api.router)


@app.get("/")
async def home(request: Request):
    return "Rainer Astodillo"
