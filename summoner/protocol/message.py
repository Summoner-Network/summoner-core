import inspect
from dataclasses import dataclass, field

# https://www.youtube.com/watch?v=vBH6GRJ1REM&ab_channel=mCoding

@dataclass(frozen=True) # does not have slots
class SummonerMessage:
    sender: str = field(default="")
    type: str = field(default="")
    payload: str = field(default="")

# server: check on validity of messages (listen and inspect) [security]
# client: encryption/decryption

if __name__ == "__main__":
    print(inspect.getmembers(SummonerMessage, inspect.isfunction))

