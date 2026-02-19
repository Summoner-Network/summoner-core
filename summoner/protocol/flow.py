"""
Handles many regexes for different ArrowStyle
"""
from __future__ import annotations
import re
from collections.abc import Callable
from typing import Iterable, Optional, Any
import warnings
from .triggers import load_triggers
from .process import Node, ArrowStyle, ParsedRoute
from ._deprecation import deprecated # type: ignore

# pylint:disable=line-too-long

# variable names or commands used in flow transitions
_TOKEN_RE = re.compile(r"""
    ^                  # start of string
    /?                 # optional leading slash
    [A-Za-z_]\w*       # identifier starting with letter or underscore
    (?:\([^)]*\))?     # optional (...) group with no nesting
    $                  # end of string
""", re.VERBOSE)

def get_token_list(input_string: str, separator: str) -> list[str]:
    """
    Tokenize a string by splitting on a top-level separator, while preserving
    any separator characters that appear within parentheses.

    This function returns a list of non-empty tokens, each trimmed of
    leading and trailing whitespace. Parenthesized substrings are never
    split—even if they contain the separator.

    Example:
        >>> get_token_list("foo,bar(baz,qux),zap", ",")
        ["foo", "bar(baz,qux)", "zap"]
    """
    split_parts: list[str] = []
    current_chars: list[str] = []
    parenthesis_depth: int = 0

    for character in input_string:

        if character == "(":
            parenthesis_depth += 1
        elif character == ")":
            parenthesis_depth = max(0, parenthesis_depth - 1)

        if character == separator and parenthesis_depth == 0:
            split_parts.append("".join(current_chars))
            current_chars = []
        else:
            current_chars.append(character)

    split_parts.append("".join(current_chars))

    return [part.strip() for part in split_parts if part.strip()]

Unpack = Callable[[re.Match[str]], tuple[str, str, str]]

# dynamic Trigger class returned by load_triggers()
TriggerType = type

class Flow:
    """
    Handles many regexes for different ArrowStyle
    """

    def __init__(self, triggers_file: Optional[str] = None) -> None:
        """
        Handles many regexes for different ArrowStyle
        """
        self.triggers_file = triggers_file
        self.in_use: bool = False
        self.arrows: set[ArrowStyle] = set()

        self._regex_ready: bool = False
        self._regex_patterns: list[tuple[re.Pattern[str], ArrowStyle, Unpack]] = []

    def activate(self) -> Flow:
        """
        In the client there is logic
        that depends on how this is toggled.
        """
        self.in_use = True
        return self

    def deactivate(self) -> Flow:
        """
        In the client there is logic
        that depends on how this is toggled.
        """
        self.in_use = False
        return self

    def add_arrow_style(
        self,
        stem: str,
        brackets: tuple[str, str],
        separator: str,
        tip: str
    ) -> None:
        """
        Add an arrow, that is another possibility
        for parsing routes.
        This means the regex patterns will need
        to be recompiled as the set of ArrowStyle has changed.
        """
        style = ArrowStyle(
            stem=stem,
            brackets=brackets,
            separator=separator,
            tip=tip
        )
        self.arrows.add(style)
        self._regex_ready = False
        self._regex_patterns.clear()

    def triggers(self, json_dict: Optional[dict[str, Any]] = None) -> TriggerType:
        """
        Load and build the TRIG class from the nested_dict or TRIGGERS file
        """
        if json_dict is None:
            if self.triggers_file is None:
                # use the file TRIGGERS
                return load_triggers()
            return load_triggers(triggers_file=self.triggers_file)
        return load_triggers(json_dict=json_dict)

    def _build_labeled_complete(
        self,
        base: str,
        left_bracket: str,
        right_bracket: str,
        tip: str
    ) -> re.Pattern[str]:
        """
        Build the regex to match
        a complete labelled arrow.
        For example,
        As --[Cs]--> Bs
        - base says the shaft of the arrow is --
        - left_bracket,right_bracket say the labels are surrounded by []
        - tip says the tip of the arrow is >
        """
        left  = rf"{base}{left_bracket}"
        right = rf"{right_bracket}{base}{tip}"
        regex = rf"""
            ^\s*
            (?P<source>.+?)\s*{left}\s*
            (?P<label>.*?)\s*{right}\s*
            (?P<target>.+?)\s*$
        """
        return re.compile(regex, re.VERBOSE)

    def _build_unlabeled_complete(self, base: str, tip: str) -> re.Pattern[str]:
        """
        Build the regex to match
        a complete unlabelled arrow.
        For example,
        As --> Bs
        - base says the shaft of the arrow is --
        - tip says the tip of the arrow is >
        """
        arrow  = rf"{base}{tip}"
        regex = rf"""
            ^\s*
            (?P<source>.+?)\s*{arrow}\s*
            (?P<target>.+?)\s*$
        """
        return re.compile(regex, re.VERBOSE)

    def _build_labeled_dangling_right(
        self,
        base: str,
        left_bracket: str,
        right_bracket: str,
        tip: str
    ) -> re.Pattern[str]:
        """
        Build the regex to match
        a dangling on the right labelled arrow.
        For example,
        As --[Cs]-->
        - base says the shaft of the arrow is --
        - left_bracket,right_bracket say the labels are surrounded by []
        - tip says the tip of the arrow is >
        """
        left  = rf"{base}{left_bracket}"
        right = rf"{right_bracket}{base}{tip}"
        regex = rf"""
            ^\s*
            (?P<source>.+?)\s*{left}\s*
            (?P<label>.*?)\s*{right}\s*$
        """
        return re.compile(regex, re.VERBOSE)

    def _build_unlabeled_dangling_right(self, base: str, tip: str) -> re.Pattern[str]:
        """
        Build the regex to match
        a dangling on the right unlabelled arrow.
        For example,
        As -->
        - base says the shaft of the arrow is --
        - tip says the tip of the arrow is >
        """
        arrow  = rf"{base}{tip}"
        regex = rf"""
            ^\s*
            (?P<source>.+?)\s*{arrow}\s*$
        """
        return re.compile(regex, re.VERBOSE)

    def _build_labeled_dangling_left(
        self,
        base: str,
        left_bracket: str,
        right_bracket: str,
        tip: str
    ) -> re.Pattern[str]:
        """
        Build the regex to match
        a dangling on the left labelled arrow.
        For example,
        --[Cs]--> Bs
        - base says the shaft of the arrow is --
        - left_bracket,right_bracket say the labels are surrounded by []
        - tip says the tip of the arrow is >
        """
        left  = rf"{base}{left_bracket}"
        right = rf"{right_bracket}{base}{tip}"
        regex = rf"""
            ^\s*
            {left}\s*
            (?P<label>.*?)\s*{right}\s*
            (?P<target>.+?)\s*$
        """
        return re.compile(regex, re.VERBOSE)

    def _build_unlabeled_dangling_left(self, base: str, tip: str) -> re.Pattern[str]:
        """
        Build the regex to match
        a dangling on the left unlabelled arrow.
        For example,
        --> Bs
        - base says the shaft of the arrow is --
        - tip says the tip of the arrow is >
        """
        arrow = rf"{base}{tip}"
        regex = rf"""
            ^\s*
            {arrow}\s*
            (?P<target>.+?)\s*$
        """
        return re.compile(regex, re.VERBOSE)

    def _unpack_labeled_complete(self, matched_pattern: re.Match[str]) -> tuple[str, str, str]:
        """
        If the arrow was complete and labelled, the produced regex will have 3 groups
        for source, label and target. A match with this will give strings for each of these.
        """
        return (
            matched_pattern.group("source"),
            matched_pattern.group("label"),
            matched_pattern.group("target"),
        )

    def _unpack_unlabeled_complete(self, matched_pattern: re.Match[str]) -> tuple[str, str, str]:
        """
        If the arrow was complete and unlabelled, the produced regex will have 2 groups
        for source and target. A match with this will give strings for each of these and the label is empty.
        """
        return matched_pattern.group("source"), "", matched_pattern.group("target")

    def _unpack_labeled_dangling_right(self, matched_pattern: re.Match[str]) -> tuple[str, str, str]:
        """
        If the arrow was dangling on the right and labelled, the produced regex will have 2 groups
        for source and label. A match with this will give strings for each of these and the target is empty.
        """
        return matched_pattern.group("source"), matched_pattern.group("label"), ""

    def _unpack_unlabeled_dangling_right(self, matched_pattern: re.Match[str]) -> tuple[str, str, str]:
        """
        If the arrow was dangling on the right and unlabelled, the produced regex will have 1 group
        for source. A match with this will give strings for each of these and the label and target are empty.
        """
        return matched_pattern.group("source"), "", ""

    def _unpack_labeled_dangling_left(self, matched_pattern: re.Match[str]) -> tuple[str, str, str]:
        """
        If the arrow was dangling on the left and labelled, the produced regex will have 2 groups
        for label and target. A match with this will give strings for each of these and the source is empty.
        """
        return "", matched_pattern.group("label"), matched_pattern.group("target")

    def _unpack_unlabeled_dangling_left(self, matched_pattern: re.Match[str]) -> tuple[str, str, str]:
        """
        If the arrow was dangling on the left and unlabelled, the produced regex will have 1 group
        for target. A match with this will give strings for each of these and the source and label are empty.
        """
        return "", "", matched_pattern.group("target")

    def _prepare_regex(self) -> None:
        """
        If the regex for all the ArrowStyle(s) have not been compiled, compile them all.
        This takes care of all variants of
        - labelled, unlabelled
        - dangling left, dangling right, complete
        on each of the styles.
        """
        if self._regex_ready:
            return

        for style in self.arrows:
            base   = re.escape(style.stem * 2)
            left_bracket, right_bracket = map(re.escape, style.brackets)
            tip    = re.escape(style.tip)

            # labeled
            pattern_complex_complete = self._build_labeled_complete(base, left_bracket, right_bracket, tip)
            self._regex_patterns.append((pattern_complex_complete, style, self._unpack_labeled_complete))

            pattern_complex_dangling_left = self._build_labeled_dangling_left(base, left_bracket, right_bracket, tip)
            self._regex_patterns.append((pattern_complex_dangling_left, style, self._unpack_labeled_dangling_left))

            pattern_complex_dangling = self._build_labeled_dangling_right(base, left_bracket, right_bracket, tip)
            self._regex_patterns.append((pattern_complex_dangling, style, self._unpack_labeled_dangling_right))

            # unlabeled
            pattern_simple_complete = self._build_unlabeled_complete(base, tip)
            self._regex_patterns.append((pattern_simple_complete, style, self._unpack_unlabeled_complete))

            pattern_simple_dangling = self._build_unlabeled_dangling_left(base, tip)
            self._regex_patterns.append((pattern_simple_dangling, style, self._unpack_unlabeled_dangling_left))

            pattern_simple_dangling_left = self._build_unlabeled_dangling_right(base, tip)
            self._regex_patterns.append((pattern_simple_dangling_left, style, self._unpack_unlabeled_dangling_right))

        self._regex_ready = True

    def _validate_tokens(self, tokens: list[str], text: str) -> None:
        """
        Each source, label and target must conform to the naming style
        similar to a Python identifier but also allowing /all /oneof /not
        which are also valid as sources and targets.
        """
        for token in tokens:
            if not _TOKEN_RE.match(token):
                raise ValueError(f"Invalid token {token!r} in route {text!r}")

    def _parse_standalone(self, text: str) -> ParsedRoute:
        """
        Similar to parsing the dangling right unlabelled case
        which is like `As ->`
        but this is just the As part without the ->
        """
        # split on commas, strip whitespace, drop empties
        source_list = get_token_list(text, separator=',')
        # validate each token
        self._validate_tokens(source_list, text)
        return ParsedRoute(
            source=tuple(Node(tok) for tok in source_list),
            label=(),
            target=(),
            style=None,
        )

    def compile_arrow_patterns(self) -> None:
        """
        Compile (or recompile) regex patterns from the current arrow styles.
        Safe to call multiple times; no effect if already compiled.
        """
        if self.in_use:
            self._prepare_regex()

    @deprecated(
        "Flow.ready() is deprecated. Use Flow.compile_arrow_patterns() instead. "
        "When using SummonerClient, patterns are compiled automatically during registration."
    )
    def ready(self) -> None:
        """
        Same as compile_arrow_patterns but the deprecated version.
        """
        warnings.warn(
            "Flow.ready() is deprecated; use Flow.compile_arrow_patterns() instead. "
            "In SummonerClient, you generally don't need to call this.",
            category=DeprecationWarning,
            stacklevel=2,
        )
        if self.in_use:
            self._prepare_regex()

    def parse_route(self, route: str) -> ParsedRoute:
        """
        route is something like As --[Bs]-> Cs
        or As --> etc among all the possibilities of
        having labels or not, being dangling or not,
        having an arrow or not.
        Give the ParsedRoute which is storing the
        sources, labels and targets with empty tuples for any
        of them that were not present.
        If present, the As, Bs, and Cs are split into tuples according
        to the separator of the relevant ArrowStyle in order
        to handle multiple sources, labels and/or targets
        """
        route = route.strip()
        if not self._regex_ready:
            self._prepare_regex()

        for pattern, style, unpack in self._regex_patterns:
            matched_pattern = pattern.match(route)
            if not matched_pattern:
                continue
            source_raw_text, label_raw_text, target_raw_text = unpack(matched_pattern)

            source_list = get_token_list(source_raw_text, style.separator)
            label_list = get_token_list(label_raw_text, style.separator)
            target_list = get_token_list(target_raw_text, style.separator)
            self._validate_tokens(source_list + label_list + target_list, route)

            return ParsedRoute(
                source=tuple(Node(tok) for tok in source_list),
                label=tuple(Node(tok) for tok in label_list),
                target=tuple(Node(tok) for tok in target_list),
                style=style,
            )

        return self._parse_standalone(route)

    def parse_routes(self, routes: Iterable[str]) -> list[ParsedRoute]:
        """
        Do parse_route for each of the routes
        """
        return [self.parse_route(route=route) for route in routes]
