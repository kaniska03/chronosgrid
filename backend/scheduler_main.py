"""Standalone scheduler service entrypoint (docker compose `scheduler`)."""
import asyncio
import logging
import signal

from app.events import bus
from app.services.scheduler_service import SchedulerService


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='{"ts":"%(asctime)s","logger":"%(name)s",'
                               '"level":"%(levelname)s","message":"%(message)s"}')
    await bus.start()
    service = SchedulerService()
    service.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await service.stop()
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
