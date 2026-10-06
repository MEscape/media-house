"""Composition root: the one place where concrete adapters meet abstract ports."""

from collections.abc import Sequence
from dataclasses import dataclass

from media_house.bootstrap.lifecycle import ShutdownStack
from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.infrastructure.system_clock import SystemClock
from media_house.core.modules import ApplicationModule, Container, install_modules
from media_house.shared.concurrency import JobScheduler, ThreadPoolJobScheduler
from media_house.shared.configuration import AppSettings
from media_house.shared.errors.handler import ErrorHandler
from media_house.shared.events import ApplicationEvent, DomainEvent, EventBus, EventPublisher
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Infrastructure:
    """Concrete adapters shared by every module."""

    error_handler: ErrorHandler
    event_bus: EventBus
    scheduler: ThreadPoolJobScheduler
    process_runner: ProcessRunner
    clock: Clock


def compose_infrastructure(settings: AppSettings, stack: ShutdownStack) -> Infrastructure:
    error_handler = ErrorHandler()
    event_bus = EventBus()
    event_bus.subscribe(DomainEvent, lambda e: _log.debug("Domain event", event=type(e).__name__))
    event_bus.subscribe(
        ApplicationEvent,
        lambda e: _log.debug("Application event", event=type(e).__name__),
    )

    scheduler = ThreadPoolJobScheduler(
        max_workers=settings.concurrency.max_workers,
        error_handler=error_handler,
    )
    timeout = settings.concurrency.shutdown_timeout_seconds
    stack.push("job scheduler", lambda: scheduler.shutdown(timeout=timeout))

    return Infrastructure(
        error_handler=error_handler,
        event_bus=event_bus,
        scheduler=scheduler,
        process_runner=SubprocessRunner(),
        clock=SystemClock(),
    )


def compose_services(
    settings: AppSettings,
    paths: AppPaths,
    infrastructure: Infrastructure,
    modules: Sequence[ApplicationModule],
) -> Container:
    """Register core services, then let every module register its own."""
    container = Container()
    container.register_instance(AppSettings, settings)
    container.register_instance(AppPaths, paths)
    container.register_instance(ErrorHandler, infrastructure.error_handler)
    container.register_instance(EventBus, infrastructure.event_bus)
    container.register_instance(EventPublisher, infrastructure.event_bus)
    container.register_instance(JobScheduler, infrastructure.scheduler)
    container.register_instance(ProcessRunner, infrastructure.process_runner)
    container.register_instance(Clock, infrastructure.clock)
    install_modules(modules, container)
    return container
