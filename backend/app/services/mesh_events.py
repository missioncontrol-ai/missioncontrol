"""
Mesh event bus — fan-out of task/agent/message events.
Backends:
- Postgres: pg_notify on write paths, asyncpg LISTEN connection for fan-out
- SQLite: in-process asyncio.Queue broadcast (dev/test)

Consumers subscribe via asyncio.Queue and receive events as dicts.
"""
import asyncio
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- Subscriber registry ----
# channel_key -> list of queues
_subscribers: Dict[str, List[asyncio.Queue]] = {}

# Reference to the running asyncio event loop, captured at startup.
# Used to safely schedule puts from sync (threadpool) handlers.
_event_loop: Optional[asyncio.AbstractEventLoop] = None


def capture_event_loop() -> None:
    """Store a reference to the current event loop. Call once from async startup."""
    global _event_loop
    try:
        _event_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass


def subscribe(channel_key: str) -> asyncio.Queue:
    """Subscribe to a channel. Returns a queue that will receive event dicts."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.setdefault(channel_key, []).append(q)
    return q


def unsubscribe(channel_key: str, q: asyncio.Queue):
    """Unsubscribe a queue from a channel."""
    subs = _subscribers.get(channel_key, [])
    try:
        subs.remove(q)
    except ValueError:
        pass
    if not subs:
        _subscribers.pop(channel_key, None)


def _publish_local(channel_key: str, event: dict):
    """Fan out an event to all local subscribers for this channel.

    Safe to call from both async contexts and sync handlers running in a
    threadpool. When called from a thread, uses call_soon_threadsafe so the
    asyncio event loop wakes up waiters correctly.
    """
    in_async_context = True
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        in_async_context = False

    for q in list(_subscribers.get(channel_key, [])):
        if in_async_context:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full for channel %s, dropping event", channel_key)
        else:
            loop = _event_loop
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(q.put_nowait, event)
            else:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("Event queue full for channel %s, dropping event", channel_key)


# ---- Publish interface (called from write paths) ----

def publish_task_event(
    event_type: str,
    task_id: str,
    kluster_id: str,
    mission_id: str,
    status: Optional[str] = None,
    **extra,
):
    """Publish a task lifecycle event. Called from work.py write paths."""
    event = {
        "type": "task_event",
        "event": event_type,
        "task_id": task_id,
        "kluster_id": kluster_id,
        "mission_id": mission_id,
        "status": status,
        **extra,
    }
    # Fan out to kluster, mission, and all-events channels
    _publish_local(f"kluster:{kluster_id}", event)
    _publish_local(f"mission:{mission_id}", event)
    _publish_local("__all_events__", event)

    # Postgres NOTIFY (if configured)
    _notify_postgres(event)


def _notify_postgres(event: dict):
    """Fire pg_notify if running on Postgres. Best-effort — never raises."""
    try:
        from app.db import engine
        if engine.dialect.name != "postgresql":
            return
        # Use a short-lived connection for NOTIFY
        with engine.connect() as conn:
            import sqlalchemy
            conn.execute(
                sqlalchemy.text("SELECT pg_notify('mesh_events', :payload)"),
                {"payload": json.dumps(event)}
            )
            conn.commit()
    except Exception as e:
        logger.debug("pg_notify failed (non-critical): %s", e)


# ---- Postgres LISTEN background task (optional) ----
# This connects a persistent asyncpg listener to fan out events from other processes.
# Only meaningful in multi-process Postgres deployments.
# For single-process (SQLite or single-worker Postgres), _publish_local is sufficient.

_listen_task: Optional[asyncio.Task] = None


async def _listen_postgres_loop(dsn: str):
    """Background listener — receives pg_notify from other processes and fans out locally."""
    try:
        import asyncpg  # optional dependency
    except ImportError:
        logger.debug("asyncpg not available; cross-process LISTEN disabled")
        return

    while True:
        try:
            conn = await asyncpg.connect(dsn)

            async def on_notify(conn, pid, channel, payload):
                try:
                    event = json.loads(payload)
                    kluster_id = event.get("kluster_id")
                    mission_id = event.get("mission_id")
                    if kluster_id:
                        _publish_local(f"kluster:{kluster_id}", event)
                    if mission_id:
                        _publish_local(f"mission:{mission_id}", event)
                except Exception as e:
                    logger.warning("Failed to process pg_notify payload: %s", e)

            await conn.add_listener("mesh_events", on_notify)
            logger.info("Postgres LISTEN started on mesh_events channel")

            # Keep alive until cancelled
            while True:
                await asyncio.sleep(30)
                await conn.execute("SELECT 1")  # keepalive
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("Postgres listener error: %s, reconnecting in 5s", e)
            await asyncio.sleep(5)


def start_postgres_listener(dsn: str):
    """Start background Postgres LISTEN task. Call from lifespan if Postgres."""
    global _listen_task
    _listen_task = asyncio.create_task(_listen_postgres_loop(dsn), name="mesh-events-listener")


def stop_postgres_listener():
    global _listen_task
    if _listen_task and not _listen_task.done():
        _listen_task.cancel()
