"""The one place errors become HTTP responses."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from packages.exchange import ExchangeError

STATUS = {"ip_not_whitelisted": 401, "unauthorized": 401, "invalid_api_key": 401, "no_api_key": 401,
          "network": 503, "not_found": 404}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def install(app: FastAPI) -> None:
    @app.exception_handler(ExchangeError)
    async def _exchange(_: Request, e: ExchangeError):
        return JSONResponse({"error": {"code": e.code, "message": e.message or str(e)}},
                            status_code=STATUS.get(e.code, e.http_status or 502))

    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status)

    @app.exception_handler(ValueError)
    async def _value(_: Request, e: ValueError):
        return JSONResponse({"error": {"code": "invalid", "message": str(e)}}, status_code=422)
