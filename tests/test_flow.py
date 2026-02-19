"""
Tests for flow.py: token splitting and route parsing.
"""

import pytest
from summoner.protocol.flow import get_token_list, Flow
from summoner.protocol.process import Node, ParsedRoute

@pytest.mark.parametrize("input_str, sep, expected", [
    ("foo,bar(baz,qux),zap", ",", ["foo", "bar(baz,qux)", "zap"]),
    (" a , b ,c ", ",", ["a", "b", "c"]),
    ("one(two,three,four),five", ",", ["one(two,three,four)", "five"]),
])
def test_get_token_list(input_str, sep, expected):
    """
    Only top-level separators should split
    """
    assert get_token_list(input_str, sep) == expected


def make_flow():
    """
    Helper to set up a Flow with two arrow styles
    """
    flow = Flow()
    flow.activate()
    flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
    flow.add_arrow_style(stem="=", brackets=("{", "}"), separator=";", tip=")")
    flow.compile_arrow_patterns()
    return flow


def test_parse_route_complete_simple():
    """
    A simple ParsedRoute
    """
    flow = make_flow()
    pr : ParsedRoute = flow.parse_route("A --> B")
    # Expect source A, no label, target B
    assert pr.source == (Node("A"),)
    assert pr.label == ()
    assert pr.target == (Node("B"),)
    assert pr.is_arrow

def test_parse_route_unlabeled_complete():
    """
    A simple ParsedRoute that is explicitly unlabeled
    """
    flow = make_flow()
    pr = flow.parse_route("X--[]-->Y")
    assert pr.source == (Node("X"),)
    assert pr.target == (Node("Y"),)
    assert pr.label == ()
    assert pr.is_arrow

def test_parse_route_complete_labelled():
    """
    A simple ParsedRoute
    """
    flow = make_flow()
    pr = flow.parse_route("A --[e,f,g]--> B")
    # Expect source A, (e,f,g) label, target B
    assert pr.source == (Node("A"),)
    assert pr.label == (Node("e"),Node("f"), Node("g"))
    assert pr.target == (Node("B"),)
    assert pr.is_arrow

def test_parse_route_dangling_right():
    """
    A dangling right ParsedRoute
    """
    flow = make_flow()
    pr = flow.parse_route("-->B")
    # Dangling left: no source, no label
    assert pr.source == ()
    assert pr.target == (Node("B"),)
    assert pr.is_initial


def test_parse_route_dangling_left():
    """
    A dangling left ParsedRoute
    """
    flow = make_flow()
    pr = flow.parse_route("A-->")
    assert pr.source == (Node("A"),)
    assert pr.target == ()


def test_parse_route_standalone():
    """
    Standalone object ParsedRoute
    """
    flow = make_flow()
    pr = flow.parse_route("X, Y ,Z")
    # Comma-separated standalone objects
    assert pr.source == (Node("X"), Node("Y"), Node("Z"))
    assert not pr.is_arrow and pr.is_object


def test_parse_route_invalid_token():
    """
    An invalid target causes the creation of ParsedRoute
    to fail
    """
    flow = make_flow()
    with pytest.raises(ValueError):
        flow.parse_route("A --> inv&alid")


def test_parse_routes_list():
    """
    Make several ParsedRoutes
    """
    flow = make_flow()
    routes = ["A-->B", "C"]
    prs = flow.parse_routes(routes)
    assert isinstance(prs, list)
    assert prs[0] == flow.parse_route("A-->B")
    assert prs[1] == flow.parse_route("C")
