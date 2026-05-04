from pydantic import BaseModel, Field, HttpUrl


class ShortenRequest(BaseModel):
    url: HttpUrl = Field(..., description="The long URL to shorten (max 2048 chars)")

    model_config = {"json_schema_extra": {"examples": [{"url": "https://example.com/some/long/path"}]}}


class ShortenResponse(BaseModel):
    short_code: str = Field(..., description="The generated 7-character code")
    short_url: str = Field(..., description="Full short URL ready to share")
    long_url: str = Field(..., description="Original URL that was shortened")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "short_code": "6qmtOsk",
                    "short_url": "http://localhost:8000/6qmtOsk",
                    "long_url": "https://example.com/some/long/path",
                }
            ]
        }
    }


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Human-readable error message")
