"""Separate rejected model instructions from controller and recording failures."""


class ToolRejected(ValueError):
    """A valid controller rejected a model-facing tool argument or precondition."""


class InvalidModelResponse(ValueError):
    """A provider reply does not satisfy the requested response schema."""


class RecordingError(RuntimeError):
    """A paid attempt could not be durably recorded; its cost is still charged."""


class ControllerError(RuntimeError):
    """An integrity, storage or implementation failure stopped the controller."""
