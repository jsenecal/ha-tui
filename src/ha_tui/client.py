"""Async Home Assistant WebSocket API client."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

log = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Any]


class HAError(RuntimeError):
    pass


class HAAuthError(HAError):
    pass


class HAConnectionError(HAError):
    pass


class HACommandError(HAError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def ws_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :] + "/api/websocket"
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :] + "/api/websocket"
    raise HAConnectionError(f"Invalid Home Assistant URL: {base_url!r}")


class HAClient:
    def __init__(self, url: str, token: str, verify_ssl: bool = True, timeout: float = 30) -> None:
        self.url = url
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.ha_version: str | None = None
        self._ws: ClientConnection | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, EventCallback] = {}
        self._reader: asyncio.Task | None = None
        self._disconnect_callbacks: list[Callable[[], Any]] = []
        self._closing = False
        self._tasks: set[asyncio.Future] = set()

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._reader is not None and not self._reader.done()

    def on_disconnect(self, callback: Callable[[], Any]) -> None:
        self._disconnect_callbacks.append(callback)

    async def connect(self) -> None:
        ssl_ctx: ssl.SSLContext | None = None
        uri = ws_url(self.url)
        if uri.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
            if not self.verify_ssl:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        self._closing = False
        self._ids = itertools.count(1)
        self._subscriptions.clear()
        try:
            self._ws = await connect(uri, ssl=ssl_ctx, max_size=None, open_timeout=self.timeout, ping_interval=30)
            hello = json.loads(await self._ws.recv())
            if hello.get("type") != "auth_required":
                raise HAConnectionError(f"Unexpected greeting from Home Assistant: {hello}")
            await self._ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(await self._ws.recv())
        except (OSError, InvalidHandshake, InvalidURI, ConnectionClosed, TimeoutError) as exc:
            await self._drop_socket()
            raise HAConnectionError(f"Could not connect to {self.url}: {exc}") from exc
        if auth.get("type") != "auth_ok":
            await self._drop_socket()
            raise HAAuthError(f"Authentication rejected: {auth.get('message', auth)}")
        self.ha_version = auth.get("ha_version")
        # Batch outgoing results into fewer frames, as the frontend does.
        await self._ws.send(
            json.dumps({"id": next(self._ids), "type": "supported_features", "features": {"coalesce_messages": 1}})
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def _drop_socket(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def close(self) -> None:
        self._closing = True
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None
        await self._drop_socket()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                for msg in data if isinstance(data, list) else [data]:
                    self._handle(msg)
        except ConnectionClosed:
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HAConnectionError("Connection to Home Assistant lost"))
            self._pending.clear()
            if not self._closing:
                log.warning("Home Assistant connection lost")
                for cb in self._disconnect_callbacks:
                    cb()

    def _handle(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        if msg.get("type") == "result":
            fut = self._pending.pop(msg_id, None)
            if fut is None or fut.done():
                return
            if msg.get("success", False):
                fut.set_result(msg.get("result"))
            else:
                err = msg.get("error") or {}
                fut.set_exception(HACommandError(err.get("code", "unknown"), err.get("message", "")))
        elif msg.get("type") == "event":
            cb = self._subscriptions.get(msg_id)
            if cb is None:
                return
            try:
                result = cb(msg.get("event", {}))
                if isinstance(result, Awaitable):
                    task = asyncio.ensure_future(result)
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            except Exception:
                log.exception("Subscription callback failed")

    async def send(self, type: str, **payload: Any) -> Any:
        msg_id = next(self._ids)
        return await self._request(msg_id, type, payload)

    async def _request(self, msg_id: int, type: str, payload: dict[str, Any]) -> Any:
        if self._ws is None:
            raise HAConnectionError("Not connected to Home Assistant")
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send(json.dumps({"id": msg_id, "type": type, **payload}))
            return await asyncio.wait_for(fut, self.timeout)
        except ConnectionClosed as exc:
            raise HAConnectionError("Connection to Home Assistant lost") from exc
        finally:
            self._pending.pop(msg_id, None)

    async def subscribe(
        self, callback: EventCallback, type: str = "subscribe_events", **payload: Any
    ) -> Callable[[], Awaitable[None]]:
        """Start a subscription; returns an async function that cancels it."""
        msg_id = next(self._ids)
        # Register before the ack arrives: render_template sends its first
        # event right behind the result, sometimes in the same frame.
        self._subscriptions[msg_id] = callback
        try:
            await self._request(msg_id, type, payload)
        except BaseException:
            self._subscriptions.pop(msg_id, None)
            raise

        async def unsubscribe() -> None:
            if self._subscriptions.pop(msg_id, None) is not None and self.connected:
                with contextlib.suppress(HAError):
                    await self.send("unsubscribe_events", subscription=msg_id)

        return unsubscribe

    # ------------------------------------------------------------------ #
    async def get_states(self) -> list[dict[str, Any]]:
        return await self.send("get_states")

    async def get_config(self) -> dict[str, Any]:
        return await self.send("get_config")

    async def get_services(self) -> dict[str, dict[str, Any]]:
        return await self.send("get_services")

    async def entity_registry_display(self) -> dict[str, Any]:
        return await self.send("config/entity_registry/list_for_display")

    async def device_registry(self) -> list[dict[str, Any]]:
        return await self.send("config/device_registry/list")

    async def area_registry(self) -> list[dict[str, Any]]:
        return await self.send("config/area_registry/list")

    async def floor_registry(self) -> list[dict[str, Any]]:
        try:
            return await self.send("config/floor_registry/list")
        except HACommandError:
            return []

    async def list_dashboards(self) -> list[dict[str, Any]]:
        return await self.send("lovelace/dashboards/list")

    async def lovelace_config(self, url_path: str | None = None) -> dict[str, Any]:
        payload = {"url_path": url_path} if url_path else {}
        return await self.send("lovelace/config", **payload)

    async def subscribe_states(
        self, callback: Callable[[dict[str, Any], dict[str, Any] | None], Any]
    ) -> Callable[[], Awaitable[None]]:
        """callback(new_state, old_state); new_state is {"entity_id": ..., "state": None} on removal."""

        def on_event(event: dict[str, Any]) -> Any:
            data = event.get("data", {})
            new = data.get("new_state") or {"entity_id": data.get("entity_id"), "state": None, "attributes": {}}
            return callback(new, data.get("old_state"))

        return await self.subscribe(on_event, event_type="state_changed")

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
        return_response: bool = False,
    ) -> Any:
        payload: dict[str, Any] = {"domain": domain, "service": service, "service_data": data or {}}
        if target:
            payload["target"] = target
        if return_response:
            payload["return_response"] = True
        return await self.send("call_service", **payload)

    async def subscribe_template(self, template: str, callback: Callable[[str], Any]) -> Callable[[], Awaitable[None]]:
        def on_event(event: dict[str, Any]) -> Any:
            if "result" in event:
                return callback(str(event["result"]))
            if "error" in event:
                return callback(f"[template error] {event['error']}")

        return await self.subscribe(
            on_event, type="render_template", template=template, strict=False, report_errors=True
        )

    async def render_template(self, template: str) -> str:
        loop = asyncio.get_running_loop()
        first: asyncio.Future[str] = loop.create_future()
        unsub = await self.subscribe_template(template, lambda r: first.done() or first.set_result(r))
        try:
            return await asyncio.wait_for(first, self.timeout)
        finally:
            await unsub()

    async def history(self, entity_id: str, hours: float = 24) -> list[dict[str, Any]]:
        start = datetime.now(UTC) - timedelta(hours=hours)
        result = await self.send(
            "history/history_during_period",
            start_time=start.isoformat(),
            entity_ids=[entity_id],
            minimal_response=True,
            no_attributes=True,
            significant_changes_only=False,
        )
        return result.get(entity_id, [])
