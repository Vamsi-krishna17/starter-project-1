from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.azure_openai_adapter import LLMError
from app.api.orders import router
from app.config import get_settings
from app.logging_config import configure_logging
from loguru import logger
from app.repositories.product_repository import RepositoryError

configure_logging(get_settings().log_level)

app = FastAPI(title="Email-to-Order Workflow")
app.include_router(router)


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    logger.error("LLM error: {}", exc)
    return JSONResponse(status_code=502, content={"error": "llm_unavailable", "detail": str(exc)})


@app.exception_handler(RepositoryError)
async def repo_error_handler(request: Request, exc: RepositoryError):
    logger.error("Repository error: {}", exc)
    return JSONResponse(status_code=503, content={"error": "database_unavailable", "detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}
