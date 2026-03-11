from collections import defaultdict
from typing import Any

import anyio
from fastapi import WebSocket


class RealtimeHub:
    def __init__(self) -> None:
        self._account_connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._conversation_connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._management_connections: set[WebSocket] = set()

    async def connect_account(self, websocket: WebSocket, *, account_id: int) -> None:
        await websocket.accept()
        self._account_connections[account_id].add(websocket)

    async def connect_management(self, websocket: WebSocket) -> None:
        self._management_connections.add(websocket)

    def disconnect(self, websocket: WebSocket, *, account_id: int | None = None) -> None:
        if account_id is not None and account_id in self._account_connections:
            self._account_connections[account_id].discard(websocket)
            if not self._account_connections[account_id]:
                del self._account_connections[account_id]

        self._management_connections.discard(websocket)

        stale_conversations: list[int] = []
        for conversation_id, sockets in self._conversation_connections.items():
            sockets.discard(websocket)
            if not sockets:
                stale_conversations.append(conversation_id)
        for conversation_id in stale_conversations:
            del self._conversation_connections[conversation_id]

    def subscribe_conversation(self, websocket: WebSocket, *, conversation_id: int) -> None:
        self._conversation_connections[conversation_id].add(websocket)

    async def _send_to_sockets(
        self,
        sockets: list[WebSocket],
        payload: dict[str, Any],
        *,
        account_ids: set[int] | None = None,
    ) -> None:
        stale: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_json(payload)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(socket)
            if account_ids:
                for account_id in account_ids:
                    connections = self._account_connections.get(account_id)
                    if connections and socket in connections:
                        self.disconnect(socket, account_id=account_id)

    async def send_account_event(self, account_id: int, payload: dict[str, Any]) -> None:
        sockets = list(self._account_connections.get(account_id, set()))
        await self._send_to_sockets(sockets, payload, account_ids={account_id})

    async def broadcast_conversation(self, conversation_id: int, payload: dict[str, Any]) -> None:
        sockets = list(self._conversation_connections.get(conversation_id, set()))
        await self._send_to_sockets(sockets, payload)

    async def broadcast_chat_event(
        self,
        conversation_id: int,
        account_ids: list[int],
        payload: dict[str, Any],
    ) -> None:
        sockets: set[WebSocket] = set(self._conversation_connections.get(conversation_id, set()))
        for account_id in account_ids:
            sockets.update(self._account_connections.get(account_id, set()))
        await self._send_to_sockets(list(sockets), payload, account_ids=set(account_ids))

    async def broadcast_management_event(self, payload: dict[str, Any]) -> None:
        sockets = list(self._management_connections)
        await self._send_to_sockets(sockets, payload)


realtime_hub = RealtimeHub()


def run_async_from_sync(coro, *args):
    return anyio.from_thread.run(coro, *args)
