"""Integrity-aware text input: hash exactly the bytes the analysis consumes.

Why this exists
---------------
The pipeline used to hash the input in one full read, analyse it in a second
read, and (when incidents were found) re-hash it in a third read before a
fourth read for contributor evidence. The hash and the analysed bytes were
therefore not guaranteed to describe the same content: a file modified between
the hash and the analysis pass would be analysed but reported under the old
hash.

``HashedTextFile`` removes that gap by construction. Every byte that reaches
the text decoder passes through a SHA-256 object first, so the digest is a
digest of what was actually analysed. It also removes one or two full reads.

Decoding behaviour is intentionally identical to
``open(path, "r", encoding="utf-8", errors="replace")``: the same
FileIO -> BufferedReader -> TextIOWrapper stack with universal newlines, so
record boundaries and replacement characters are unchanged.

Standard library only; Python 3.9+.
"""

from __future__ import annotations

import hashlib
import io
from typing import Optional

_CHUNK = 65536


class _HashingRaw(io.RawIOBase):
    """Raw reader that feeds every byte it returns into a hash object."""

    def __init__(self, raw: io.RawIOBase, hasher: "hashlib._Hash") -> None:
        super().__init__()
        self._raw = raw
        self._hasher = hasher
        self.eof_seen = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[override]
        n = self._raw.readinto(buffer)
        if n:
            self._hasher.update(memoryview(buffer)[:n])
        elif n == 0:
            self.eof_seen = True
        return n

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class HashedTextFile:
    """Context manager exposing ``.stream`` (text) and ``.sha256_hexdigest()``.

    ``sha256_hexdigest()`` drains any bytes the consumer did not read (for
    example when ingestion stops early at its record cap), so the digest covers
    the whole file. If the consumer already read to end-of-file, nothing more is
    read: bytes appended afterwards were never analysed and are not hashed, so
    the digest always describes the content that was actually consumed. Call it
    before leaving the ``with`` block.
    """

    def __init__(self, path: str) -> None:
        self._hasher = hashlib.sha256()
        self._raw = open(path, "rb", buffering=0)
        try:
            self._hashing = _HashingRaw(self._raw, self._hasher)
            self._buffered = io.BufferedReader(self._hashing, buffer_size=_CHUNK)
            self.stream = io.TextIOWrapper(
                self._buffered, encoding="utf-8", errors="replace"
            )
        except BaseException:
            self._raw.close()
            raise
        self._digest: Optional[str] = None

    def sha256_hexdigest(self) -> str:
        if self._digest is None:
            # Everything already pulled into the buffers has been hashed. If end-of-file
            # was not reached (consumer stopped early), pull the rest so the digest
            # covers the whole file; if it was reached, do not read past it.
            if not self._hashing.eof_seen:
                while self._buffered.read(_CHUNK):
                    pass
            self._digest = self._hasher.hexdigest()
        return self._digest

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> "HashedTextFile":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
