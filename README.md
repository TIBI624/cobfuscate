# CObfuscate

Python code obfuscator with C backend support.

## Installation

**PC:**
```bash
pip install cobfuscate
```
(very long process if don't have installed Cryptography)

**Termux:**
*Please check you using GitHub or F-Droid version.*
1. Updating pkg
```bash
pkg update && pkg upgrade
```

2. Install deps and Python
```bash
pkg install python python-pip python-cryptography clang build-essential
```

3. Install CObfuscate
```bash
pip install cobfuscate
```

4. Setup memory access
```bash
termux-setup-storage 
```

## Usage

```bash
# Obfuscate single file
cobfuscate input.py output.py

# Obfuscate directory
cobfuscate ./src ./obfuscated
```

## Requirements

- Python 3.7+
- clang (for C extension)

## License

MIT