import json
import re
import time
from typing import Any, Dict, Tuple, Optional

TYPE_REGEX = re.compile(r'(?:type|interface)\s+(\w+)\s*=*\s*(\{[\s\S]*\});?')
FIELD_REGEX = re.compile(r'(\w+)\s*(\?)?\s*:\s*((?:Array<)?\{[\s\S]*?\}(?:>)?|[^;]+);')
INLINE_REGEX = re.compile(r'(Array<)?\{([\s\S]*)\}(>)?')
ARRAY_REGEX = re.compile(r'(\[\]|Array<|>)')
UNION_REGEX = re.compile(r'\s*\|\s*')

CACHE_INLINE = {}

def compile_typescript_type(ts_type: str) -> Tuple[bool, Optional[Dict], Optional[str]]:
    ts_type = re.sub(r'\/\*[\s\S]*?\*\/|\/\/.*', '', ts_type)

    type_match = TYPE_REGEX.search(ts_type.strip())
    if not type_match:
        return False, None, "Invalid TypeScript type definition"

    type_name, type_body = type_match.groups()
    fields = {}

    for field_name, optional_marker, field_type in FIELD_REGEX.findall(type_body):
        field_type = field_type.strip()
        is_array = field_type.endswith('[]') or field_type.startswith('Array<')
        is_inline_object = bool(INLINE_REGEX.match(field_type))

        compiled_inline_type = None
        if is_inline_object:
            inline_body_match = INLINE_REGEX.search(field_type)
            inline_body = inline_body_match.group(2)
            cache_key = inline_body
            if cache_key in CACHE_INLINE:
                compiled_inline_type = CACHE_INLINE[cache_key]
            else:
                success, compiled_inline_type, error = compile_typescript_type(f"type Inline={{ {inline_body} }}")
                if not success:
                    return False, None, error
                CACHE_INLINE[cache_key] = compiled_inline_type

        fields[field_name] = {
            'type': field_type,
            'is_optional': bool(optional_marker),
            'is_array': is_array,
            'is_inline_object': is_inline_object,
            'compiled_inline_type': compiled_inline_type
        }

    return True, {'name': type_name, 'fields': fields}, None

def validate(json_data: str, compiled_type: Dict) -> Tuple[bool, Optional[str]]:
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    try:
        validate_against_type(data, compiled_type)
        return True, None
    except ValueError as e:
        return False, str(e)

def validate_against_type(data: Any, type_info: Dict, path: str = "") -> None:
    if not isinstance(data, dict):
        raise ValueError(f"Expected object at '{path}', got {type(data).__name__}")

    fields = type_info['fields']
    for field_name, field_info in fields.items():
        if field_name not in data:
            if not field_info['is_optional']:
                raise ValueError(f"Missing required field '{path}.{field_name}'")
            continue

        value = data[field_name]
        current_path = f"{path}.{field_name}" if path else field_name

        if field_info['is_array']:
            if not isinstance(value, list):
                raise ValueError(f"Field '{current_path}' should be array")

            if field_info['is_inline_object']:
                elem_type = field_info['compiled_inline_type']
                for idx, elem in enumerate(value):
                    validate_against_type(elem, elem_type, f"{current_path}[{idx}]")
            else:
                elem_type_str = ARRAY_REGEX.sub('', field_info['type']).strip()
                for idx, elem in enumerate(value):
                    validate_basic_type(elem, elem_type_str, f"{current_path}[{idx}]")

        elif field_info['is_inline_object']:
            validate_against_type(value, field_info['compiled_inline_type'], current_path)
        else:
            validate_basic_type(value, field_info['type'], current_path)

def validate_basic_type(value: Any, type_str: str, path: str) -> None:
    if '|' in type_str:
        subtypes = UNION_REGEX.split(type_str)
        if not any(validate_single_type(value, subtype.strip()) for subtype in subtypes):
            raise ValueError(f"Value at '{path}' doesn't match union types: {type_str}")
        return

    if not validate_single_type(value, type_str.strip()):
        raise ValueError(f"Value at '{path}' should be {type_str}")

def validate_single_type(value: Any, type_str: str) -> bool:
    return ((type_str == 'number' and isinstance(value, (int, float)) and not isinstance(value, bool)) or
            (type_str == 'string' and isinstance(value, str)) or
            (type_str == 'boolean' and isinstance(value, bool)) or
            (type_str == 'any') or
            (type_str == 'null' and value is None) or
            (type_str == 'object' and isinstance(value, dict)))

# Example usage
if __name__ == "__main__":
    ts_type_user = """
    type User = {
        id: number;
        name: string;
        email?: string;
        active: boolean;
        tags: string[];
    };
    """

    _, compiled_user, _ = compile_typescript_type(ts_type_user)

    valid_json_user = '{"id":1,"name":"Alice","active":true,"tags":["dev"]}'
    invalid_json_user = '{"id":"1","active":true,"tags":["dev"]}'

    print(validate(valid_json_user, compiled_user))
    print(validate(invalid_json_user, compiled_user))

    ts_type_write = """
    type Write = {
        idempotency: number;
        context: Array<{ type: number; guid: number; version: number; }>;
        operations: Array<{ type: number; guid: number; version: number; value: any; }>;
    };
    """

    _, compiled_write, _ = compile_typescript_type(ts_type_write)

    valid_json_write = '{"idempotency":123,"context":[{"type":1,"guid":101,"version":1}],"operations":[{"type":1,"guid":101,"version":2,"value":{"key":"value"}}]}'
    print(validate(valid_json_write, compiled_write))

    invalid_json_write = '{"idempotency":123,"context":[{"type":1,"guid":101,"version":1}],"operations":[{"type":1,"guid":"a","version":2,"value":{"key":"value"}}]}'
    print(validate(invalid_json_write, compiled_write))

    start_time = time.time()
    for _ in range(1000000):
        validate(valid_json_write, compiled_write)
    end_time = time.time()

    print(f"Benchmark (1M runs): {end_time - start_time:.4f} seconds")

    nasty_ts_type = """
    type Extreme = {
        id: number | string;
        data?: Array<{ nested?: { deep: number[] | null; }; } | null>;
        flags: boolean[];
        complex: Array<{ x: number; y?: Array<{ z: string | null; }>; } | null>;
        anything: any;
    };
    """

    _, compiled_extreme, _ = compile_typescript_type(nasty_ts_type)

    nasty_json = json.dumps({
        "id": "1234",
        "data": [{"nested": {"deep": [1, 2, 3]}}, None],
        "flags": [True, False, True],
        "complex": [{"x": 10, "y": [{"z": None}]}],
        "anything": {"really": ["anything"]}
    })

    start_time = time.time()
    for _ in range(1000000):
        validate(nasty_json, compiled_extreme)
    end_time = time.time()

    print(f"Extreme Benchmark (1M runs): {end_time - start_time:.4f} seconds")