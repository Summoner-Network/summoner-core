from summoner.protocol.triggers import Event, Action
from summoner.protocol.flow import Flow
from summoner.protocol.triggers import (
    Action, 
    Event, 
    Move, 
    Stay, 
    Test, 
    extract_signal, 
)
from typing import Type



flow = Flow()
flow.activate()
Trigger = flow.triggers()
print("Trigger.OK:", Trigger.OK)
print("Trigger.acceptable:", Trigger.acceptable)
print("Trigger.error:", Move(Trigger.error))
print("Trigger.major:", Trigger.major)
print("Trigger.OK > Trigger.acceptable:", Trigger.OK > Trigger.acceptable)
print("Trigger.OK > Trigger.major:", Trigger.OK > Trigger.major)
print("Name for (1, 1):", Trigger.name_of(1, 1))
print("Move(Trigger.error):", Move(Trigger.error))
print("Extract signal from Move(Trigger.error):", extract_signal(Move(Trigger.error)))
assert Trigger.OK > Trigger.acceptable
assert not (Trigger.OK > Trigger.major)
assert Trigger.name_of(0, 2) == "all_good"
assert Trigger.name_of(1, 0) == "minor"
print(isinstance(Move(Trigger.OK), Action.MOVE))
print(isinstance(Stay(Trigger.OK), Action.STAY))
print(isinstance(Test(Trigger.OK), Action.TEST))
print(isinstance(Move(Trigger.OK), Action.STAY))
print(extract_signal(Move(Trigger.error)) == Trigger.error)
print(extract_signal(Move(Trigger.error)) > Trigger.major)
print(extract_signal(Move(Trigger.error)) < Trigger.OK)
print(type(Action.MOVE), 
      isinstance(Action.MOVE, Type), 
      issubclass(Action.MOVE, Event), 
      Action.MOVE in [Action.MOVE, Action.STAY, Action.TEST]
      )

flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
flow.add_arrow_style(stem="=", brackets=("{", "}"), separator=";", tip=")")
flow.compile_arrow_patterns()
print(flow.arrows)
tests = [
    "/all",
    "A --> B",
    "/all =={f;h;t}==) B;C",
    "/not(E,F); /all; /not(D) =={/not(B)}==) /not(E,R)",
    "E =={f}==)",
    " ==) F ",
    " --[ t]--> A ",
    "x--[]-->",
    "A, C, D --[ f, g, h ]--> B, K, F",
    "A, C, D --[ ]--> B, K, F",
    " X =={ foo ; bar }==) Y ; Z ",
    "StandaloneLabel",
    " A",
    "B ",
    '    F    ',
    "A, C, D -- [ ] -- > B, K, F",
    "Bad -[ style ]-> MissingTip",
    "A --> inv&alid",
    "A =={ foo }==) bar>oops",
]

for t in tests:
    try:
        result = flow.parse_routes([t])
        print("Arrow:", result[0].is_arrow)
        print("Object:", result[0].is_object)
        print(f"\033[92mInput: {t!r}\n  → Parsed: {result}\033[0m\n")
    except Exception as e:
        print(f"\033[91mInput: {t!r}  raised {type(e).__name__}: {e}\033[0m\n")
