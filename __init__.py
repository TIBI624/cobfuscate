"""CObfuscate - Python code obfuscator with C backend support."""

__version__ = "0.3.0"
__author__ = "WinFun15"

from .obfuscator import obfuscate_file, obfuscate_directory

__all__ = ["obfuscate_file", "obfuscate_directory", "__version__"]