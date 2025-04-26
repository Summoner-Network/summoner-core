def remove_last_newline(s: str):
    return s[:-1] if s.endswith('\n') else s

def ensure_trailing_newline(s: str):
    return s if s.endswith('\n') else s + '\n'

import json

def fully_recover_json(data):
    """
    Recursively recover original nested structure from JSON strings.

    Args:
        data (any): Data structure possibly containing nested JSON-encoded strings.

    Returns:
        The original fully-recovered nested data structure.
    """
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            # Continue recursively
            return fully_recover_json(parsed)
        except json.JSONDecodeError:
            # If parsing fails, return the original string
            return data
    elif isinstance(data, list):
        return [fully_recover_json(elem) for elem in data]
    elif isinstance(data, dict):
        return {key: fully_recover_json(val) for key, val in data.items()}
    else:
        # Base case: primitive data type
        return data

