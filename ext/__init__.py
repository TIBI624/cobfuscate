try:
    from .obfuscate import obfuscate_string, obfuscate_code
except ImportError:
    obfuscate_string = None
    obfuscate_code = None

__all__ = ["obfuscate_string", "obfuscate_code"]