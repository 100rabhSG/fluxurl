from fastapi import FastAPI

app = FastAPI(title="Fluxurl")


@app.get("/")
async def root() -> dict[str, str]:
    return {"hello": "world"}
