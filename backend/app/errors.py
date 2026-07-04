"""Structured error format shared by every endpoint."""
import uuid

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str,
                 details: dict | None = None, headers: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        self.correlation_id = str(uuid.uuid4())
        super().__init__(status_code=status_code, detail=message, headers=headers)

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details,
                          "correlation_id": self.correlation_id}}


def not_found(resource: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{resource} not found")


def forbidden(message: str = "You do not have permission to perform this action") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


def conflict(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(409, code, message, details)


def bad_request(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(400, code, message, details)


def too_many(message: str, retry_after: float, details: dict | None = None) -> ApiError:
    return ApiError(429, "RATE_LIMITED", message, details,
                    headers={"Retry-After": str(max(1, int(retry_after)))})
