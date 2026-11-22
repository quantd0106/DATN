import httpx
from pydantic import AnyHttpUrl, BaseModel, SecretStr

from app.common.errors import AppError


def service_headers(key: SecretStr | None, correlation_id: str) -> dict[str, str]:
    if key is None:
        raise AppError(503)
    return {"X-Service-Key": key.get_secret_value(), "X-Correlation-ID": correlation_id}


async def request_model[ResponseModel: BaseModel](
    client: httpx.AsyncClient,
    origin: AnyHttpUrl | None,
    path: str | None,
    model: type[ResponseModel],
    *,
    headers: dict[str, str],
    method: str = "GET",
    params: dict | None = None,
    payload: dict | None = None,
    expected_statuses: tuple[int, ...] = (200,),
    public_core: bool = False,
) -> ResponseModel:
    if origin is None or path is None:
        raise AppError(503)
    try:
        response = await client.request(
            method,
            f"{str(origin).rstrip('/')}{path}",
            headers=headers,
            params=params,
            json=payload,
        )
        if public_core and response.status_code in (401, 403, 404):
            raise AppError(response.status_code)
        if response.status_code not in expected_statuses:
            raise AppError(503)
        return model.model_validate_json(response.content)
    except (httpx.HTTPError, ValueError):
        raise AppError(503) from None
