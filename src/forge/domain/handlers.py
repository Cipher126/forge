import inspect
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from forge.errors.handlers import HandlerRegistryError, UnknownHandlerError

JOB_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ExecutionMode(StrEnum):
    ASYNC = "async"
    THREAD = "thread"
    PROCESS = "process"


@dataclass(frozen=True, slots=True)
class HandlerDefinition:
    job_type: str
    version: str
    payload_model: type[BaseModel]
    handler: Callable[..., object]
    execution_mode: ExecutionMode
    timeout_seconds: int
    default_max_attempts: int

    def validate_payload(self, payload: Mapping[str, Any]) -> BaseModel:
        return self.payload_model.model_validate(payload)


class HandlerRegistry:
    """In-memory manifest of handler code deployed in this process."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], HandlerDefinition] = {}
        self._active_versions: dict[str, str] = {}

    @property
    def active_versions(self) -> Mapping[str, str]:
        return MappingProxyType(self._active_versions)

    def __iter__(self) -> Iterator[HandlerDefinition]:
        return iter(self._definitions.values())

    def register(
        self,
        *,
        job_type: str,
        version: str,
        payload_model: type[BaseModel],
        handler: Callable[..., object],
        execution_mode: ExecutionMode,
        timeout_seconds: int = 300,
        default_max_attempts: int = 5,
        active: bool = False,
    ) -> HandlerDefinition:
        if JOB_TYPE_PATTERN.fullmatch(job_type) is None:
            raise HandlerRegistryError(
                "job type must be dot-separated lowercase identifiers, for example "
                "'reports.generate'"
            )
        if VERSION_PATTERN.fullmatch(version) is None:
            raise HandlerRegistryError("handler version contains unsupported characters")
        if timeout_seconds < 1:
            raise HandlerRegistryError("handler timeout must be at least one second")
        if default_max_attempts < 1:
            raise HandlerRegistryError("default max attempts must be at least one")
        if execution_mode is ExecutionMode.ASYNC and not inspect.iscoroutinefunction(handler):
            raise HandlerRegistryError("an async handler must be declared with async def")
        if execution_mode is not ExecutionMode.ASYNC and inspect.iscoroutinefunction(handler):
            raise HandlerRegistryError("thread and process handlers must be synchronous")

        key = (job_type, version)
        if key in self._definitions:
            raise HandlerRegistryError(f"handler {job_type!r} version {version!r} is duplicated")

        definition = HandlerDefinition(
            job_type=job_type,
            version=version,
            payload_model=payload_model,
            handler=handler,
            execution_mode=execution_mode,
            timeout_seconds=timeout_seconds,
            default_max_attempts=default_max_attempts,
        )
        self._definitions[key] = definition
        if active:
            self.activate(job_type, version)
        return definition

    def activate(self, job_type: str, version: str) -> None:
        if (job_type, version) not in self._definitions:
            raise UnknownHandlerError(f"handler {job_type!r} version {version!r} is not registered")
        self._active_versions[job_type] = version

    def resolve(self, job_type: str, version: str | None = None) -> HandlerDefinition:
        resolved_version = version if version is not None else self._active_versions.get(job_type)
        if resolved_version is None:
            raise UnknownHandlerError(f"job type {job_type!r} has no active handler")
        try:
            return self._definitions[(job_type, resolved_version)]
        except KeyError as error:
            raise UnknownHandlerError(
                f"handler {job_type!r} version {resolved_version!r} is not registered"
            ) from error
