class DummyWriter:
    def __init__(self):
        self.messages: list[bytes] = []
        self.drain_calls = 0

    def write(self, data: bytes) -> None:
        self.messages.append(data)

    async def drain(self) -> None:
        self.drain_calls += 1
