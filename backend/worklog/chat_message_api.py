"""HTTP surface for messages to a chat's agents: the person writes and confirms who works in the chat, agents receive.
Agents may only take their inbox (policy.ROUTES); a chat's messages and the composer stay with the person."""

from collections.abc import Callable

from fastapi import FastAPI, Query, Request

from . import models as m
from .chat_messages import ChatMessageService
from .db import Database


def install_chat_message_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = ChatMessageService()

    @app.get("/api/v1/chats/{identity}/messages")
    def chat_messages(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return db.snapshot(lambda c: service.messages(c, identity, limit, offset))

    @app.post("/api/v1/chats/{identity}/messages")
    def chat_message_create(identity: str, request: Request, body: m.ChatMessageCreate) -> dict:
        return mutate(request, body.model_dump(), lambda c, a: service.post(c, a, identity, body.model_dump()), required_key=True)

    # The person's word on who works in a chat; agents are refused before reaching these.
    @app.post("/api/v1/chats/{identity}/confirmations")
    def chat_confirmation_create(identity: str, request: Request, body: m.ChatConfirmation) -> dict:
        return mutate(request, body.model_dump(), lambda c, a: service.confirm(c, a, identity, body.model_dump()), required_key=True)

    @app.post("/api/v1/chats/{identity}/confirmations/remove")
    def chat_confirmation_remove(identity: str, request: Request, body: m.ChatConfirmation) -> dict:
        return mutate(request, body.model_dump(), lambda c, a: service.unconfirm(c, a, identity, body.model_dump()), required_key=True)

    @app.post("/api/v1/inbox/receive")
    def inbox_receive(request: Request, body: m.InboxReceive) -> dict:
        # An empty receive changes nothing, so polling leaves no receipts behind.
        return mutate(request, body.model_dump(), lambda c, a: service.receive(c, a, body.model_dump()),
                      required_key=True, receipt_if=lambda result: result["total"] > 0)
