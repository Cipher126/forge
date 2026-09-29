class HandlerError(Exception):
    """Base class for registered-handler failures."""


class HandlerRegistryError(HandlerError, ValueError):
    """The deployed handler configuration is invalid."""


class UnknownHandlerError(HandlerError, LookupError):
    """No deployed handler matches the requested type and version."""
