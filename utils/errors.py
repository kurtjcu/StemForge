"""
Custom exception hierarchy for StemForge.

All application-specific exceptions inherit from :class:`StemForgeError`
so that callers can catch the entire family with a single ``except`` clause
when broad error handling is appropriate.

Exception classes
-----------------
StemForgeError
    Base class for all StemForge exceptions.

ModelLoadError
    Raised when a model cannot be loaded from disk or a remote cache.

AudioProcessingError
    Raised when an audio read, write, or transform operation fails.

PipelineExecutionError
    Raised when a pipeline fails during its ``run()`` call.

InvalidInputError
    Raised when caller-supplied input fails validation before processing
    begins.
"""


class StemForgeError(Exception):
    """Base class for all StemForge application exceptions."""


class ModelLoadError(StemForgeError):
    """Raised when a model cannot be loaded.

    Examples of triggering conditions
    ----------------------------------
    - The requested model identifier is not recognised.
    - The weight file is missing, corrupt, or incompatible.
    - Insufficient GPU/CPU memory to load the model.
    - Network failure while downloading weights from a remote cache.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    model_name:
        Identifier of the model that failed to load, if known.
    """

    def __init__(self, message: str, model_name: str | None = None) -> None:
        super().__init__(message)
        self.model_name = model_name


class AudioProcessingError(StemForgeError):
    """Raised when an audio read, write, or transformation operation fails.

    Examples of triggering conditions
    ----------------------------------
    - The input file format is not supported or is corrupt.
    - A resampling operation produces an empty or invalid waveform.
    - Writing an output file fails due to permissions or disk space.
    - Sample-rate mismatch that cannot be resolved automatically.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    path:
        Filesystem path associated with the failed operation, if applicable.
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class PipelineExecutionError(StemForgeError):
    """Raised when a pipeline fails during its ``run()`` call.

    Examples of triggering conditions
    ----------------------------------
    - The model was not loaded before ``run()`` was called.
    - Inference produced ``NaN`` or infinite values.
    - An unexpected exception propagated out of the model forward pass.
    - The pipeline was cancelled externally before completion.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    pipeline_name:
        Name of the pipeline class that raised the error, if known.
    """

    def __init__(self, message: str, pipeline_name: str | None = None) -> None:
        super().__init__(message)
        self.pipeline_name = pipeline_name


class InvalidInputError(StemForgeError):
    """Raised when caller-supplied input fails validation.

    This exception is thrown *before* any processing begins, so callers
    can distinguish user/configuration errors from runtime failures.

    Examples of triggering conditions
    ----------------------------------
    - An audio file path does not exist or has an unsupported extension.
    - A text prompt is empty or exceeds the model's maximum token length.
    - A threshold value is outside the allowed range (e.g. not in 0 – 1).
    - A required configuration field was not set before calling ``run()``.

    Parameters
    ----------
    message:
        Human-readable description of why the input is invalid.
    field:
        Name of the invalid field or parameter, if known.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field
