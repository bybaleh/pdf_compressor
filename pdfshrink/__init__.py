"""pdfshrink — сжатие PDF до заданного размера (по умолчанию 10 МБ).

Инструмент использует Ghostscript для пересжатия PDF и подбирает минимально
агрессивный профиль качества, при котором файл укладывается в целевой размер.
"""

from .compressor import (  # noqa: F401
    LADDER,
    CompressionError,
    Profile,
    Result,
    compress_pdf,
    format_size,
    parse_size,
)

__version__ = "1.0.0"
__all__ = [
    "LADDER",
    "CompressionError",
    "Profile",
    "Result",
    "compress_pdf",
    "format_size",
    "parse_size",
    "__version__",
]
