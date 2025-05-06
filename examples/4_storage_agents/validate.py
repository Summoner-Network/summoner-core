import json
import re
from typing import Any, Dict, Tuple, Optional

def compile_typescript_type(typescript_type: str) -> Dict:
    """
    Compiles a TypeScript type definition into an intermediate representation.
    """
    typescript_type = re.sub(r'\/\*[\s\S]*?\*\/|\/\/.*', '', typescript_type)

    type_match = re.search(r'(?:type|interface)\s+(\w+)\s*=*\s*(\{[\s\S]*\});?', typescript_type.strip())

    if not type_match:
        raise ValueError("Invalid TypeScript type definition")

    type_name, type_body = type_match.groups()

    fields = {}

    # Improved regex to capture inline objects properly
    field_pattern = r'(\w+)\s*(\?)?\s*:\s*((?:Array<)?\{[\s\S]*?\}(?:>)?|[^;]+);'
    field_matches = re.finditer(field_pattern, type_body)

    for match in field_matches:
        field_name, optional_marker, field_type = match.groups()
        field_type = field_type.strip()

        is_array = field_type.endswith('[]') or field_type.startswith('Array<')
        is_inline_object = bool(re.match(r'(Array<)?\{[\s\S]*\}(>)?', field_type))

        compiled_inline_type = None
        if is_inline_object:
            inline_body_match = re.search(r'\{([\s\S]*)\}', field_type)
            if inline_body_match:
                inline_body = inline_body_match.group(0)
                compiled_inline_type = compile_typescript_type(f"type InlineType = {inline_body}")

        fields[field_name] = {
            'type': field_type,
            'is_optional': bool(optional_marker),
            'is_array': is_array,
            'is_inline_object': is_inline_object,
            'compiled_inline_type': compiled_inline_type
        }

    return {'name': type_name, 'fields': fields}

def validate(json_data: str, compiled_type: Dict) -> Tuple[bool, Optional[str]]:
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {str(e)}"

    try:
        validate_against_type(data, compiled_type)
        return True, None
    except ValueError as e:
        return False, str(e)

def validate_against_type(data: Any, type_info: Dict, path: str = "") -> None:
    if not isinstance(data, dict):
        raise ValueError(f"Expected an object at '{path}', got {type(data).__name__}")

    for field_name, field_info in type_info['fields'].items():
        current_path = f"{path}.{field_name}" if path else field_name

        if field_name not in data:
            if not field_info['is_optional']:
                raise ValueError(f"Missing required field: '{current_path}'")
            continue

        field_value = data[field_name]
        field_type = field_info['type']

        if field_info['is_array']:
            if not isinstance(field_value, list):
                raise ValueError(f"Field '{current_path}' should be an array")

            if field_info['is_inline_object']:
                element_type = field_info['compiled_inline_type']
                for idx, elem in enumerate(field_value):
                    validate_against_type(elem, element_type, f"{current_path}[{idx}]")
            else:
                element_type_str = re.sub(r'(\[\]|Array<|>)', '', field_type).strip()
                for idx, elem in enumerate(field_value):
                    validate_basic_type(elem, element_type_str, f"{current_path}[{idx}]")

        elif field_info['is_inline_object']:
            validate_against_type(field_value, field_info['compiled_inline_type'], current_path)
        else:
            validate_basic_type(field_value, field_type, current_path)

def validate_basic_type(value: Any, type_str: str, path: str) -> None:
    type_str = type_str.strip()

    if '|' in type_str:
        for subtype in map(str.strip, type_str.split('|')):
            try:
                validate_basic_type(value, subtype, path)
                return
            except ValueError:
                continue
        raise ValueError(f"Value at '{path}' doesn't match any type in union: {type_str}")

    validators = {
        'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        'string': lambda v: isinstance(v, str),
        'boolean': lambda v: isinstance(v, bool),
        'any': lambda v: True,
        'null': lambda v: v is None,
        'object': lambda v: isinstance(v, dict),
    }

    if type_str in validators:
        if not validators[type_str](value):
            raise ValueError(f"Value at '{path}' should be a {type_str}")

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

    compiled_user = compile_typescript_type(ts_type_user)

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

    compiled_write = compile_typescript_type(ts_type_write)

    valid_json_write = '{"idempotency":123,"context":[{"type":1,"guid":101,"version":1}],"operations":[{"type":1,"guid":101,"version":2,"value":{"key":"value"}}]}'
    print(validate(valid_json_write, compiled_write))

    invalid_json_write = '{"idempotency":123,"context":[{"type":1,"guid":101,"version":1}],"operations":[{"type":1,"guid":"a","version":2,"value":{"key":"value"}}]}'
    print(validate(invalid_json_write, compiled_write))