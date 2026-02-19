"""
Newline manipulation utilities
"""

def remove_last_newline(s: str):
    """
    if ending with a newline, remove it
    """
    return s[:-1] if s.endswith('\n') else s

def ensure_trailing_newline(s: str):
    """
    Make sure ends with a newline
    """
    return s if s.endswith('\n') else s + '\n'
