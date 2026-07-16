"""Process bootstrap for one registered messaging gateway."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from agent.agent import DEFAULT_MAX_ITERATIONS
from agent.config import load_settings
from agent.event_log import log_gateway
from agent.gateway.daemon import clear_gateway_pid
from agent.gateway.registry import GATEWAY_INTEGRATIONS, get_gateway_integration
from agent.gateway.service import GatewayService
from agent.logging_setup import setup_logging
from agent.providers import build_provider
from agent.providers.base import ProviderError
from agent.storage.store import open_session_store

logger = logging.getLogger(__name__)


async def run_gateway(
    *, gateway_id: str = "telegram", yolo: bool = False,
    max_iterations: int = 30,
) -> int:
    integration = get_gateway_integration(gateway_id)
    if integration is None:
        available = ", ".join(sorted(
            item.definition.id for item in GATEWAY_INTEGRATIONS
        ))
        logger.error(
            "Unknown gateway %r. Available gateways: %s.",
            gateway_id,
            available,
        )
        return 2
    dependency_error = integration.dependency_error()
    if dependency_error:
        logger.error("%s", dependency_error)
        return 2
    gateway_settings = integration.load_settings()
    errors = integration.validate_settings(gateway_settings)
    if errors:
        for error in errors:
            logger.error("%s", error)
        logger.error("Run `akvan gateway` to configure the gateway.")
        return 2
    try:
        settings = load_settings(prompt_for_missing_key=False)
        provider = build_provider(settings)
    except (ValueError, ProviderError) as exc:
        logger.error("Configuration error: %s", exc)
        return 2
    store = open_session_store()
    if store is None:
        logger.error("Session database not available.")
        provider.close()
        return 2
    service = GatewayService(
        settings=settings,
        gateway_id=integration.definition.id,
        gateway_name=integration.definition.name,
        runtime_config=integration.runtime_config(gateway_settings),
        access_policy=integration.access_policy(gateway_settings),
        provider=provider,
        store=store,
        adapter=integration.create_adapter(gateway_settings),
        yolo=yolo,
        max_iterations=max_iterations,
    )
    await service.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    logger.info("Akvan %s gateway is running.", integration.definition.name)
    log_gateway(f"gateway {gateway_id} started")
    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        await service.stop()
        clear_gateway_pid(gateway_id)
    return 0


def main(
    *, gateway_id: str = "telegram", yolo: bool = False,
    max_iterations: int = 30,
) -> int:
    setup_logging(mode="gateway", gateway_id=gateway_id)
    return asyncio.run(run_gateway(
        gateway_id=gateway_id, yolo=yolo, max_iterations=max_iterations,
    ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run an Akvan messaging gateway.")
    parser.add_argument(
        "--gateway-id",
        default=os.environ.get("AKVAN_GATEWAY_ID", "telegram"),
        help="Registered gateway id to run (default: telegram).",
    )
    parser.add_argument("--yolo", action="store_true")
    parser.add_argument(
        "--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS,
    )
    args = parser.parse_args()
    raise SystemExit(main(
        gateway_id=args.gateway_id,
        yolo=args.yolo,
        max_iterations=args.max_iterations,
    ))
