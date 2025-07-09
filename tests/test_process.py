"""
Tests for process.py: Node, ArrowStyle, ParsedRoute, activated_nodes, StateTape, collect_activations
"""

import pytest
from summoner.protocol.process import (
    Node,
    ArrowStyle,
    ParsedRoute,
    StateTape,
    Receiver,
    Sender,
    TapeActivation,
)
from summoner.protocol.triggers import Action, load_triggers
from summoner.protocol.flow import Flow


def test_node_parsing_and_str_repr():
    # Plain token
    n = Node("foo")
    assert n.kind == "plain" and str(n) == "foo"
    # /all wildcard
    a = Node("/all")
    assert a.kind == "all" and str(a) == "/all"
    # /not(A,B)
    notn = Node("/not(A,B)")
    assert notn.kind == "not" and set(notn.values) == {"A", "B"}
    # /oneof(X,Y)
    ony = Node("/oneof(X,Y)")
    assert ony.kind == "oneof" and set(ony.values) == {"X", "Y"}
    with pytest.raises(ValueError):
        Node("invalid(token")


@pytest.mark.parametrize("gate,state,expected", [
    (Node("/all"), Node("anything"), True),
    (Node("foo"), Node("foo"), True),
    (Node("foo"), Node("/not(foo)"), False),
    (Node("/not(foo)"), Node("bar"), True),
    (Node("/oneof(a,b)"), Node("b"), True),
])
def test_node_accepts_matrix(gate, state, expected):
    # Verify accepts logic across kinds
    assert gate.accepts(state) is expected


def test_arrowstyle_valid_and_invalid():
    # Valid style
    style = ArrowStyle("-", ("[", "]"), ",", ">")
    assert style.stem == "-"
    # Invalid stem length
    with pytest.raises(ValueError):
        ArrowStyle("--", ("[", "]"), ",", ">")
    # Empty bracket
    with pytest.raises(ValueError):
        ArrowStyle("-", ("", "]"), ",", ">")
    # Overlapping parts
    with pytest.raises(ValueError):
        ArrowStyle("-", ("[", "]"), "[", ">")
    # Forbidden char in separator
    with pytest.raises(ValueError):
        ArrowStyle("-", ("[", "]"), "-", ">")


def test_parsedroute_properties_and_repr():
    style = ArrowStyle("-", ("[", "]"), ",", ">")
    pr = ParsedRoute((Node("A"),), (), (Node("B"),), style)
    assert not pr.has_label and pr.is_arrow and not pr.is_object
    # Initial arrow
    pr2 = ParsedRoute((), (), (Node("C"),), style)
    assert pr2.is_initial
    # String form
    assert str(pr) == "A--[]-->B"


def test_activated_nodes_various_actions():
    style = ArrowStyle("-", ("[", "]"), ",", ">")
    pr = ParsedRoute((Node("A"),), (Node("L"),), (Node("B"),), style)
    # Build a dummy trigger/event to pass
    from summoner.protocol.triggers import Move as MoveEvt, Test as TestEvt, Stay as StayEvt, load_triggers
    Trigger = load_triggers(json_dict={"d": None})
    move_event = MoveEvt(Trigger.d)
    assert pr.activated_nodes(move_event) == (Node("L"), Node("B"))
    test_event = TestEvt(Trigger.d)
    assert pr.activated_nodes(test_event) == (Node("L"),)
    stay_event = StayEvt(Trigger.d)
    assert pr.activated_nodes(stay_event) == (Node("A"),)


def test_statetape_and_revert_extend_refresh():
    # SINGLE
    tape1 = StateTape("X")
    assert tape1.revert() == [Node("X")]
    # MANY
    tape2 = StateTape(["A", "B"])
    assert tape2.revert() == [Node("A"), Node("B")]
    # INDEX_SINGLE
    tape3 = StateTape({"k": "V"})
    assert tape3.revert() == {"k": [Node("V")]}  # prefix stripped
    # INDEX_MANY
    tape4 = StateTape({"k": ["A", "B"]})
    assert tape4.revert() == {"k": [Node("A"), Node("B")]}
    # Test extend and refresh
    tape4.extend({"k": ["C"]})
    assert tape4.revert()["k"][-1] == Node("C")
    fresh = tape4.refresh()
    assert fresh.revert()["k"] == []


def test_collect_activations_simple_case():
    # Setup flow and parsed route for "A --> B"
    flow = Flow().activate()
    flow.add_arrow_style("-", ("[", "]"), ",", ">")
    flow.ready()
    pr = flow.parse_route("A --> B")
    # Fake receiver function
    async def fn(msg):
        return None
    receiver_index = {str(pr): Receiver(fn=fn, priority=(1,))}
    parsed_routes = {str(pr): pr}
    # Tape with nodes A and C under key 'k'
    tape = StateTape({"k": ["A", "C"]})
    activations = tape.collect_activations(receiver_index, parsed_routes)
    # Only A should match
    assert (1,) in activations
    acts = activations[(1,)]
    assert len(acts) == 1
    act = acts[0]
    assert act.key == "tape:k"
    assert act.state == Node("A")
    assert act.route == pr
    assert act.fn is fn


def test_sender_responds_to_filters():
    # Setup a simple Trigger set with two signals
    Trigger = load_triggers(json_dict={"X": None, "Y": None})
    sigX, sigY = Trigger.X, Trigger.Y
    from summoner.protocol.triggers import Move as MoveEvt, Stay as StayEvt, Test as TestEvt
    # Events
    move_evt = MoveEvt(sigX)
    stay_evt = StayEvt(sigY)
    test_evt = TestEvt(sigX)

    # 1) No filters → always responds
    sender1 = Sender(fn=lambda: None, multi=False, actions=None, triggers=None)
    assert sender1.responds_to(move_evt)
    assert sender1.responds_to(stay_evt)

    # 2) on_actions only
    sender_actions = Sender(fn=lambda: None, multi=False, actions={Action.MOVE}, triggers=None)
    assert sender_actions.responds_to(move_evt)
    assert not sender_actions.responds_to(stay_evt)

    # 3) on_triggers only — any event carrying sigX is accepted
    sender_triggers = Sender(fn=lambda: None, multi=False, actions=None, triggers={sigX})
    assert sender_triggers.responds_to(move_evt)
    assert sender_triggers.responds_to(test_evt)      # ← change to True
    assert not sender_triggers.responds_to(stay_evt)  # different signal Y

    # 4) both filters
    sender_both = Sender(fn=lambda: None, multi=False, actions={Action.STAY}, triggers={sigY})
    assert sender_both.responds_to(stay_evt)
    assert not sender_both.responds_to(StayEvt(sigX))
    assert not sender_both.responds_to(move_evt)
