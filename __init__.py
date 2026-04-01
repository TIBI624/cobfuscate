"""CObfuscate - Python code obfuscator with C backend support."""

__version__ = "0.1.0"
__author__ = "WinFun15"
__email__ = "tibipocoxzsa@gmail.com"

from .obfuscator import obfuscate_file, obfuscate_directory

__all__ = ["obfuscate_file", "obfuscate_directory", "__version__"]