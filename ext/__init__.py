try:
    from .obfuscate import obfuscate_string_b64
except ImportError:
    # This allows the Python part to gracefully handle the C module not being built.
    obfuscate_string_b64 = None

__all__ = ["obfuscate_string_b64"]