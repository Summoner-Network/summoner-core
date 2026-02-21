"""
Tests for triggers.py:
- signal-tree parsing
- Trigger class
- Signal ordering
- Event classes
- extract_signal
"""

import pytest
from summoner.protocol.triggers import (
    parse_signal_tree_lines,
    load_triggers,
    Signal,
    Event,
    Move,
    Stay,
    Test,
    Action,
    extract_signal,
)


def test_parse_signal_tree_simple_hierarchy():
    """
    parse_signal_tree on a simple example
    """
    # Define lines simulating a TRIGGERS file with two levels
    lines = [
        "OK\n",
        "    acceptable\n",
        "    all_good\n"
    ]
    tree = parse_signal_tree_lines(lines, tabsize=4)
    # Root key exists and children simplify to None leaves
    assert "OK" in tree
    assert tree["OK"] == {"acceptable": None, "all_good": None}


def test_parse_signal_tree_invalid_varname():
    """
    parse_signal_tree but when the input has an
    invalid name
    """
    # Names must be valid Python identifiers
    lines = [
        "123invalid\n"
    ]
    with pytest.raises(ValueError) as excinfo:
        parse_signal_tree_lines(lines, tabsize=4)
    assert "invalid name" in str(excinfo.value)


def test_parse_signal_tree_inconsistent_indent():
    """
    Indentation syntax error of the parse_signal_tree
    """
    # Indents must follow previous levels exactly or match existing levels
    lines = [
        "OK\n", 
        "    acceptable\n", 
        "  all_good\n"
    ]
    with pytest.raises(ValueError) as excinfo:
        parse_signal_tree_lines(lines, tabsize=4)
    assert "Inconsistent indent" in str(excinfo.value)


def test_parse_signal_tree_duplicate_name():
    """
    Duplicate names at same indent level are disallowed
    """
    lines = [
        "OK\n", 
        "    acceptable\n", 
        "    acceptable\n"
    ]
    with pytest.raises(ValueError) as excinfo:
        parse_signal_tree_lines(lines, tabsize=4)
    assert "duplicate signal name" in str(excinfo.value)


#pylint:disable=no-member
def test_load_triggers_with_json_dict():
    """
    loading triggers from a json_dict
    has all the information provided
    in the constructed Trigger
    """
    # Provide a nested dict directly to load_triggers
    json_dict = {"root": {"child": None}}
    Trigger = load_triggers(json_dict=json_dict)
    # Ensure attributes and path mappings are correct
    assert hasattr(Trigger, "root") and hasattr(Trigger, "child")
    assert Trigger.root.path == (0,) # pyright: ignore[reportAttributeAccessIssue]
    assert Trigger.child.path == (0, 0) # pyright: ignore[reportAttributeAccessIssue]
    assert Trigger.name_of(0, 0) == "child" # pyright: ignore[reportAttributeAccessIssue]


def test_load_triggers_reserved_keyword():
    """Reserved Python keyword names should raise an error"""
    json_dict = {"class": None}
    with pytest.raises(ValueError) as excinfo:
        load_triggers(json_dict=json_dict)
    assert "reserved or invalid" in str(excinfo.value)


def test_signal_comparison_and_properties():
    """
    Comparison of signals based on ancestry relation
    """
    # Simple hierarchy: A -> B (implemented so that ancestor > descendant)
    json_dict = {"A": {"B": None}}
    Trigger = load_triggers(json_dict=json_dict)
    #pylint:disable=invalid-name
    sigA, sigB = Trigger.A, Trigger.B # pyright: ignore[reportAttributeAccessIssue]
    # A parent signal compares greater than its child
    assert sigA > sigB
    assert sigB < sigA
    # Equality and hashing based on path
    assert sigA == Trigger.A # pyright: ignore[reportAttributeAccessIssue]
    assert hash(sigA) == hash(Trigger.A) # pyright: ignore[reportAttributeAccessIssue]
    # repr, name, path
    assert repr(sigA) == "<Signal 'A'>"
    assert sigA.name == "A"
    assert sigA.path == (0,)
    assert sigA.path == (0,)



def test_event_and_action_classes_and_extract_signal():
    """
    extract_signal
    """
    # Instantiate via a single-signal trigger
    Trigger = load_triggers(json_dict={"X": None})
    #pylint:disable=invalid-name
    sigX : Signal = Trigger.X # pyright: ignore[reportAttributeAccessIssue]
    move_evt = Move(sigX)
    stay_evt = Stay(sigX)
    test_evt = Test(sigX)
    # Check class hierarchy
    assert isinstance(move_evt, Event) and isinstance(move_evt, Move)
    assert isinstance(stay_evt, Stay) and isinstance(test_evt, Test)
    # Action class constants
    assert Action.MOVE is Move
    assert Action.STAY is Stay
    assert Action.TEST is Test
    # extract_signal correctness
    assert extract_signal(move_evt) is sigX
    assert extract_signal(sigX) is sigX
    assert extract_signal(None) is None
    with pytest.raises(TypeError):
        extract_signal(123)
