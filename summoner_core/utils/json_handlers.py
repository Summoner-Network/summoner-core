import json
from pathlib import Path
from typing import Any

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

def load_config(config_path: str, debug: bool = False) -> dict[str, Any]:
    """
    Load a JSON configuration file safely.

    Args:
        config_path (str): Path to the JSON configuration file.
        debug (bool): If True, print debug messages to the terminal.

    Returns:
        Dict[str, Any]: Parsed configuration as a dictionary.
                        Returns an empty dict if file does not exist or is invalid.
    """
    path = Path(config_path)

    if not path.is_file():
        if debug:
            print(f"[DEBUG] Config file not found: {config_path}")
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            config = json.load(f)
            if debug:
                print(f"[DEBUG] Loaded config from: {config_path}")
            return config
    except json.JSONDecodeError as e:
        if debug:
            print(f"[DEBUG] JSON decode error in {config_path}: {e}")
    except OSError as e:
        if debug:
            print(f"[DEBUG] OS error reading {config_path}: {e}")
    except Exception as e:
        if debug:
            print(f"[DEBUG] Unexpected error loading {config_path}: {e}")

    return {}
