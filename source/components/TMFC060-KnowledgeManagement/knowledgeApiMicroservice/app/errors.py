"""Error responses: the TMF630 Error extended with the RFC 7807 members, as specified in the TMFxxx OAS."""
from fastapi import HTTPException


class ApiError(HTTPException):
    """An HTTP error rendered as a TMF630/RFC 7807 Error body."""

    def __init__(self, status: int, code: str, reason: str, message: str = "", *,
                 problem_type: str = "about:blank", headers: dict[str, str] | None = None):
        super().__init__(status_code=status, detail=message or reason, headers=headers)
        self.code = code
        self.reason = reason
        self.message = message or reason
        self.problem_type = problem_type

    def body(self, instance: str) -> dict:
        return {
            "@type": "Error",
            "code": self.code,
            "reason": self.reason,
            "message": self.message,
            "status": str(self.status_code),
            "type": self.problem_type,
            "title": self.reason,
            "detail": self.message,
            "instance": instance,
        }
