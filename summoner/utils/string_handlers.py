def remove_last_newline(s: str):
    return s[:-1] if s.endswith('\n') else s

def ensure_trailing_newline(s: str):
    return s if s.endswith('\n') else s + '\n'


