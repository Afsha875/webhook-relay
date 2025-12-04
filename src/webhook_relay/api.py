"""FastAPI application exposing the admin API and the ingest endpoint.

The HTTP layer is a thin adapter over :class:`~webhook_relay.service.RelayService`.
It reads the *raw* request body (never a re-serialized JSON) so the bytes used to
verify the HMAC are exactly the bytes the sender signed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from webhook_relay.sender import HttpxSender, Sender
from webhook_relay.service import (
    InvalidSignatureError,
    RelayService,
    UnknownEndpointError,
)
from webhook_relay.store import SqliteStore

SIGNATURE_HEADER = "X-Signature"
EVENT_ID_HEADER = "X-Event-Id"


class RegisterEndpointRequest(BaseModel):
    destination_url: str = Field(..., description="Where verified events are relayed.")
    secret: str = Field(..., min_length=1, description="Shared HMAC-SHA256 secret.")


class EndpointResponse(BaseModel):
    id: str
    destination_url: str
    created_at: datetime


class DeliveryResponse(BaseModel):
    id: str
    endpoint_id: str
    event_id: str
    status: str
    attempt_count: int
    next_retry_at: datetime | None
    last_error: str | None
    updated_at: datetime


class IngestResponse(BaseModel):
    event_id: str
    status: str
    attempt_count: int
    duplicate: bool


def _endpoint_response(
    destination_url: str, endpoint_id: str, created_at: datetime
) -> EndpointResponse:
    return EndpointResponse(id=endpoint_id, destination_url=destination_url, created_at=created_at)


def get_service(request: Request) -> RelayService:
    service = request.app.state.service
    assert isinstance(service, RelayService)
    return service


ServiceDep = Annotated[RelayService, Depends(get_service)]


def create_app(service: RelayService) -> FastAPI:
    """Build the FastAPI app around an already-constructed service."""
    app = FastAPI(
        title="webhook-relay",
        version="0.1.0",
        summary="HMAC-verified webhook ingestion and relay with retry/backoff.",
    )
    app.state.service = service

    @app.post("/endpoints", response_model=EndpointResponse, status_code=201)
    def register_endpoint(
        body: RegisterEndpointRequest,
        svc: ServiceDep,
    ) -> EndpointResponse:
        endpoint = svc.register_endpoint(body.destination_url, body.secret)
        return _endpoint_response(endpoint.destination_url, endpoint.id, endpoint.created_at)

    @app.get("/endpoints", response_model=list[EndpointResponse])
    def list_endpoints(svc: ServiceDep) -> list[EndpointResponse]:
        return [
            _endpoint_response(e.destination_url, e.id, e.created_at) for e in svc.list_endpoints()
        ]

    @app.get("/deliveries", response_model=list[DeliveryResponse])
    def list_deliveries(
        svc: ServiceDep,
        endpoint_id: str | None = None,
    ) -> list[DeliveryResponse]:
        return [
            DeliveryResponse(
                id=d.id,
                endpoint_id=d.endpoint_id,
                event_id=d.event_id,
                status=d.status.value,
                attempt_count=d.attempt_count,
                next_retry_at=d.next_retry_at,
                last_error=d.last_error,
                updated_at=d.updated_at,
            )
            for d in svc.list_deliveries(endpoint_id)
        ]

    @app.post("/ingest/{endpoint_id}", response_model=IngestResponse)
    async def ingest(
        endpoint_id: str,
        request: Request,
        svc: ServiceDep,
    ) -> IngestResponse:
        raw_body = await request.body()
        signature = request.headers.get(SIGNATURE_HEADER)
        event_id = request.headers.get(EVENT_ID_HEADER)
        try:
            result = svc.ingest(endpoint_id, raw_body, signature, event_id)
        except UnknownEndpointError as exc:
            raise HTTPException(status_code=404, detail="unknown endpoint") from exc
        except InvalidSignatureError as exc:
            raise HTTPException(status_code=401, detail="invalid signature") from exc
        return IngestResponse(
            event_id=result.delivery.event_id,
            status=result.delivery.status.value,
            attempt_count=result.delivery.attempt_count,
            duplicate=result.duplicate,
        )

    return app


def build_default_app(db_path: str = "webhook_relay.db") -> FastAPI:
    """Production wiring: SQLite file + real HTTP sender."""
    store = SqliteStore(db_path)
    sender: Sender = HttpxSender()
    service = RelayService(store, sender)
    return create_app(service)
