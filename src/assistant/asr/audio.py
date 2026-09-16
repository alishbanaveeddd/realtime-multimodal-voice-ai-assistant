"""Audio wire contract for the ASR stage (approved decision, `docs` D9).

The approved contract is **PCM16 / 16 kHz / mono**:

* ``encoding`` = ``linear16`` (Deepgram's name for signed 16-bit PCM)
* 16 000 samples per second
* 1 channel (mono)

Because raw binary frames carry no format header, the byte-level invariant we
can enforce here is that every frame is a whole number of 16-bit samples. The
sample rate/channels are explicit parameters of the ASR session and are passed
verbatim to providers; they are not inferred from bytes.
"""

from __future__ import annotations

#: Codec name passed to providers (Deepgram uses "linear16" for PCM16).
ENCODING: str = "linear16"
#: Approved sample rate in Hz.
SAMPLE_RATE_HZ: int = 16000
#: Approved channel count.
CHANNELS: int = 1
#: Bytes per 16-bit PCM sample.
BYTES_PER_SAMPLE: int = 2


class AudioFrameInvalidError(ValueError):
    """Raised when an inbound audio frame violates the PCM16 contract."""


def validate_audio_frame(data: bytes) -> None:
    """Validate ``data`` as a PCM16 audio frame; raise on invalid input.

    Rejects empty frames and frames whose byte length is not a whole number of
    samples. All valid frames are therefore a multiple of ``BYTES_PER_SAMPLE``.
    """
    if not data:
        raise AudioFrameInvalidError("audio frame is empty")
    if len(data) % BYTES_PER_SAMPLE != 0:
        raise AudioFrameInvalidError(
            f"audio frame length {len(data)} is not a whole number of "
            f"{BYTES_PER_SAMPLE}-byte PCM16 samples"
        )
