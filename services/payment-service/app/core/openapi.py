"""Patches the generated OpenAPI doc so every 4xx/5xx is application/problem+json with our Problem schema."""
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

PROBLEM_SCHEMA = {
    "title": "Problem",
    "type": "object",
    "description": "RFC 9457 problem details. Every service uses this shape for every error.",
    "required": ["type", "title", "status", "code", "correlation_id"],
    "properties": {
        "type": {"type": "string", "example": "https://tatkal.example/problems/seats-unavailable"},
        "title": {"type": "string", "example": "Not enough seats"},
        "status": {"type": "integer", "example": 409},
        "detail": {"type": "string", "example": "Requested 4 seat(s) in 3A on train 12951 for 2026-11-05, only 2 left"},
        "instance": {"type": "string", "example": "/api/v1/holds"},
        "code": {"type": "string", "description": "Stable machine-readable error code", "example": "SEATS_UNAVAILABLE"},
        "rule": {"type": "string", "description": "Business rule id, present when a BR was violated", "example": "BR1"},
        "correlation_id": {"type": "string", "example": "6f1c0d0b7d0e4a5c9a3c2f0a1b2c3d4e"},
        "errors": {
            "type": "array",
            "description": "Field errors, present when code is VALIDATION_FAILED",
            "items": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "message": {"type": "string"}},
            },
        },
    },
    "additionalProperties": True,
}

def _problem_content() -> dict:
    return {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}


def install_problem_openapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description,
                             routes=app.routes, tags=app.openapi_tags)
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas["Problem"] = PROBLEM_SCHEMA
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                for code, response in operation.get("responses", {}).items():
                    if code.isdigit() and int(code) >= 400:
                        response["content"] = _problem_content()
                        if code == "422":
                            response["description"] = "Validation failed or a business rule was violated"
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
