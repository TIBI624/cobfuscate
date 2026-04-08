
"""CObfuscate exceptions."""


class CObfuscateError(Exception):
    """Base exception for CObfuscate."""
    pass


class ObfuscationError(CObfuscateError):
    """Error during obfuscation process."""
    pass


class CExtensionError(CObfuscateError):
    """Error with C extension."""
    pass


class InvalidInputError(CObfuscateError):
    """Invalid input path or format."""
    pass


class FileOperationError(CObfuscateError):
    """Error during file read/write."""
    pass