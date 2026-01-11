import json
from pathlib import Path
from typing import Union

def load_dna_from_json(filename: str) -> dict:
    path = Path(__file__).resolve().parent / filename
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
    
def save_dna_to_json(data: Union[dict, str], filename: str) -> None:
    if isinstance(data, str):
        data_ = json.loads(data)
    else:
        data_ = data.copy()
    path = Path(__file__).resolve().parent / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(data_, f, indent=2, ensure_ascii=False)