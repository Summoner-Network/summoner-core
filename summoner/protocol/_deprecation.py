try:
    from warnings import deprecated  # Python 3.13+
except ImportError:
    from typing_extensions import deprecated  # Python <= 3.12

__all__ = ["deprecated"]