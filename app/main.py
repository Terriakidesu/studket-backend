from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.web import web

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(web)


@app.get("/")
async def home(request: Request):
    return "Rainer Astodillo"
