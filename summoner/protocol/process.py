from __future__ import annotations
import re
from collections import defaultdict
from typing import Type, Any, Optional, Union, Callable, Awaitable
from enum import Enum, auto
from dataclasses import dataclass
from .triggers import Signal, Event, Action, extract_signal


# ======= NODE (GATE/STATE) =======

# Precompiled regexes for token parsing
_PLAIN_TOKEN_RE = re.compile(r"^[A-Za-z_]\w*$")
_ALL_RE = re.compile(r"^/all$")
_NOT_RE = re.compile(r"^/not\(\s*([^)]*?)\s*\)$")
_ONEOF_RE = re.compile(r"^/oneof\(\s*([^)]*?)\s*\)$")

# Wildcard sentinel for dispatch
_WILDCARD = object()

class Node:
    __slots__ = ('expr', 'kind', 'values')

    def __init__(self, expr: str) -> None:
        _expr: str = expr.strip()
        self.kind:  str
        self.values: Optional[tuple[str]]

        if _ALL_RE.fullmatch(_expr):
            self.kind = 'all'
            self.values = None
        
        elif (found_match := _NOT_RE.fullmatch(_expr)):
            self.kind = 'not'
            items = [item.strip() for item in found_match.group(1).split(',') if item.strip()]
            self.values = tuple(items)
        
        elif (found_match := _ONEOF_RE.fullmatch(_expr)):
            self.kind = 'oneof'
            items = [item.strip() for item in found_match.group(1).split(',') if item.strip()]
            self.values = tuple(items)
        
        elif _PLAIN_TOKEN_RE.fullmatch(_expr):
            self.kind = 'plain'
            self.values = (_expr,)  
        
        else:
            raise ValueError(f"Invalid syntax for token: {_expr!r}")

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, Node) and 
            self.kind == other.kind and
            self.values == other.values
        )

    def __hash__(self) -> int:
        return hash((self.kind, self.values))

    def __repr__(self) -> str:
        return (
            f"\033[95m{type(self).__name__}\033[0m("
            f"\033[94m{str(self)}\033[0m)"
            # f"\033[94mkind\033[0m=\033[90m{self.kind!r}, "
            # f"\033[94mvalues\033[0m=\033[90m{self.values!r}\033[0m)"
        )

    def __str__(self) -> str:
        try:
            if self.kind == 'all':
                return '/all'
            elif self.kind == 'plain':
                return self.values[0]
            elif self.kind == 'not':
                return f"/not({','.join(self.values)})"
            elif self.kind == 'oneof':
                return f"/oneof({','.join(self.values)})"
            else:
                return f"<Unknown Node kind: {self.kind!r}>"
        except Exception as e:
            return f"<Invalid Node: {e}>"

    def accepts(self, state: Node) -> bool:
        if not isinstance(state, Node): 
            raise TypeError(f"Argument `state` must be Node; {state} provided")
        
        table = {
            ('all', 'all'): lambda g, s: True,
            ('all', _WILDCARD): lambda g, s: True,
            (_WILDCARD, 'all'): lambda g, s: True,
            ('plain', 'plain'): lambda g, s: g.values[0] == s.values[0],
            ('plain', 'not'):   lambda g, s: g.values[0] not in s.values,
            ('plain', 'oneof'): lambda g, s: g.values[0] in s.values,
            ('not', 'plain'):   lambda g, s: s.values[0] not in g.values,
            ('not', _WILDCARD): lambda g, s: True,
            ('oneof', 'plain'): lambda g, s: s.values[0] in g.values,
            ('oneof', 'not'):   lambda g, s: bool(set(g.values) - set(s.values)),
            ('oneof', 'oneof'): lambda g, s: bool(set(g.values) & set(s.values)),
        }

        for (gk, sk), fn in table.items():
            if (gk == self.kind or gk is _WILDCARD) and (sk == state.kind or sk is _WILDCARD):
                return fn(self, state)
            
        raise RuntimeError("Unhandled combination in Node.is_compatible_with")

# ======= ARROW STYLE =======

class ArrowStyle:
    """
    stem: single character used for the arrow shaft (e.g. '-' or '=')
    brackets: tuple for label delimiters (e.g. ('[', ']'))
    separator: string to separate multiple tokens (e.g. ',' or ';')
    tip: string used for the arrow head or terminator (e.g. '>' or ')')
    """
    __slots__ = ("stem", "brackets", "separator", "tip")

    def __init__(
        self,
        stem: str,
        brackets: tuple[str, str],
        separator: str,
        tip: str
    ) -> None:
        self.stem = stem
        self.brackets = brackets
        self.separator = separator
        self.tip = tip

        self._check_stem()
        self._check_non_empty()
        self._check_conflicts()
        self._check_separator()
        self._check_regex_safe()

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, ArrowStyle) and
            self.stem == other.stem and
            self.brackets == other.brackets and
            self.separator == other.separator and
            self.tip == other.tip
        )

    def __hash__(self) -> int:
        return hash((self.stem, self.brackets, self.separator, self.tip))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"stem={self.stem!r}, "
            f"brackets={self.brackets!r}, "
            f"separator={self.separator!r}, "
            f"tip={self.tip!r})"
        )

    def _check_stem(self) -> None:
        if not isinstance(self.stem, str) or len(self.stem) != 1:
            raise ValueError(
                f"Stem must be a single character, got {self.stem!r}"
            )

    def _check_non_empty(self) -> None:
        for name, value in (
            ("left bracket",  self.brackets[0]),
            ("right bracket", self.brackets[1]),
            ("separator",     self.separator),
            ("tip",           self.tip),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"{name!r} must be non-empty, got {value!r}"
                )

    def _check_conflicts(self) -> None:
        base  = self.stem * 2
        parts = {base, self.brackets[0], self.brackets[1], self.tip}
        for first in parts:
            for second in parts:
                if first is not second and (first in second or second in first):
                    raise ValueError(
                        f"Overlap in parts: {first!r} vs {second!r}"
                    )

    def _check_separator(self) -> None:
        """
        Ensure that the separator does not contain any characters
        that are reserved by the flow parser, including grouping
        symbols, slashes, and any symbols used elsewhere in this arrow style.
        """
        forbidden = {
            self.stem,
            self.brackets[0], self.brackets[1],
            self.tip,
            "(", ")", "/"
        }
        for char in forbidden:
            if char in self.separator:
                raise ValueError(
                    f"Separator {self.separator!r} uses forbidden char {char!r}"
                )

    def _check_regex_safe(self) -> None:
        for part in (*self.brackets, self.tip, self.separator):
            try:
                re.escape(part)
            except re.error as e:
                raise ValueError(
                    f"Part {part!r} invalid for regex: {e}"
                )

# ======= PARSED ROUTE =======

class ParsedRoute:
    __slots__ = ('source', 'label', 'target', 'style', '_string')

    def __init__(
        self,
        source: tuple[Node, ...],
        label:  tuple[Node, ...],
        target: tuple[Node, ...],
        style:  Optional[ArrowStyle]
    ) -> None:
        self.source = source
        self.label  = label
        self.target = target
        self.style  = style

        if self.is_arrow:
            src = self.style.separator.join(str(n) for n in self.source)
            lab = self.style.separator.join(str(n) for n in self.label)
            tgt = self.style.separator.join(str(n) for n in self.target)

            left  = self.style.stem * 2 + self.style.brackets[0]
            right = self.style.brackets[1] + self.style.stem * 2 + self.style.tip

            self._string = f"{src}{left}{lab}{right}{tgt}"
        else:
            self._string = ','.join(str(n) for n in self.source)

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, ParsedRoute) and
            self._string == other._string
        )

    def __hash__(self) -> int:
        return hash(self._string)

    def __repr__(self) -> str:
        return (
            f"\033[95m{type(self).__name__}\033[0m("
            f"\033[94m{self._string}\033[0m)"
            # f"\033[94msource\033[0m=\033[90m{self.source!r}, "
            # f"\033[94mlabel\033[0m=\033[90m{self.label!r}, "
            # f"\033[94mtarget\033[0m=\033[90m{self.target!r}, "
            # f"\033[94mstyle\033[0m=\033[90m{self.style!r}\033[0m)"
        )

    def __str__(self) -> str:
        return self._string

    @property
    def has_label(self) -> bool:
        return bool(self.label)

    @property
    def is_arrow(self) -> bool:
        return bool(self.target) or self.has_label

    @property
    def is_object(self) -> bool:
        return not self.is_arrow
    
    @property
    def is_initial(self) -> bool:
        return self.is_arrow and not self.source

    def activated_nodes(
        self,
        event: Optional[Event],
    ) -> tuple[Node, ...]:
        """
        Given an event of type Action.*, return the Nodes
        that this route “activates” (i.e. should be added to the tape).
        """
        if not self.is_arrow:
            # standalone → only the source nodes
            return self.source

        # arrow route → pick based on the Action subtype
        if isinstance(event, Action.MOVE):
            return self.label + self.target
        if isinstance(event, Action.TEST):
            return self.label
        if isinstance(event, Action.STAY):
            return self.source

        return ()


# ======= PROTOCOL: SEND / RECEIVE =======

@dataclass(frozen=True)
class Sender:
    __slots__ = ('fn', 'multi', 'actions', 'triggers')
    fn: Callable[[], Awaitable]
    multi: bool
    actions: Optional[set[Type]]
    triggers: Optional[set[Signal]]

    def responds_to(self, event: Any) -> bool:
        action_check = True
        if self.actions is not None:
            if not any(isinstance(event, action) for action in self.actions):
                action_check = False
            
        trigger_check = True
        if self.triggers is not None:
            if not any(extract_signal(event) == trig for trig in self.triggers):
                trigger_check = False
            
        return action_check and trigger_check

@dataclass(frozen=True)
class Receiver:
    __slots__ = ('fn', 'priority')
    fn: Callable[[Union[str, dict]], Awaitable[Optional[Event]]]
    priority: tuple[int, ...]

class Direction(Enum):
    SEND = auto()
    RECEIVE = auto()

@dataclass(frozen=True)
class TapeActivation:
    __slots__ = ('key', 'state', 'route', 'fn')
    key: Optional[str]
    state: Optional[Node]
    route: ParsedRoute
    fn: Callable[[Any], Awaitable]

# ======= STATE TAPE =======

class TapeType(Enum):
    SINGLE        = auto()
    MANY          = auto()
    INDEX_SINGLE  = auto()
    INDEX_MANY    = auto()

class StateTape:
    __slots__ = ('states', '_type')

    prefix: str = "tape"

    def __init__(self, states: Any = None, with_prefix: bool = True):
        # Figure out what kind of input we have
        tp = StateTape._assess_type(states)

        # Default: empty index-many
        if tp is None:
            self.states = {}
            self._type  = TapeType.INDEX_MANY

        # Exactly SINGLE
        elif tp is TapeType.SINGLE:
            node = self._nodeify(states)  # wrap str→Node if needed
            self.states = {self.prefix: [node]}
            self._type  = tp

        # Exactly MANY
        elif tp is TapeType.MANY:
            nodes = [self._nodeify(s) for s in states]
            self.states = {self.prefix: nodes}
            self._type  = tp

        # Exactly INDEX_SINGLE
        elif tp is TapeType.INDEX_SINGLE:
            self.states = {
                self._add_prefix(k, with_prefix): [self._nodeify(v)]
                for k, v in states.items()
            }
            self._type = tp

        # Exactly INDEX_MANY
        elif tp is TapeType.INDEX_MANY:
            self.states = {
                self._add_prefix(k, with_prefix): [self._nodeify(s) for s in v]
                for k, v in states.items()
            }
            self._type = tp

        else:
            # Should never happen, but safe-guard
            raise RuntimeError(f"Unhandled TapeType {tp!r}")

    def set_type(self, value: TapeType) -> StateTape:
        self._type = value
        return self

    @staticmethod
    def _assess_type(states: Any) -> Optional[TapeType]:
        # Scalar → SINGLE
        if isinstance(states, (str, Node)):
            return TapeType.SINGLE

        # Sequence of scalars → MANY
        if isinstance(states, (list, tuple)):
            if all(isinstance(s, (str, Node)) for s in states):
                return TapeType.MANY

        # Mapping → either INDEX_SINGLE or INDEX_MANY
        if isinstance(states, dict):
            # all values are scalar → INDEX_SINGLE
            if all(
                isinstance(k, (str, type(None)))
                and isinstance(v, (str, Node))
                for k, v in states.items()
            ):
                return TapeType.INDEX_SINGLE

            # all values are either scalar or sequence of scalars → INDEX_MANY
            if all(
                isinstance(k, (str, type(None)))
                and (
                    isinstance(v, (str, Node))
                    or (
                        isinstance(v, (list, tuple))
                        and all(isinstance(x, (str, Node)) for x in v)
                    )
                )
                for k, v in states.items()
            ):
                return TapeType.INDEX_MANY

        return None

    def _add_prefix(self, key: str, with_prefix: bool = True) -> str:
        return f"{self.prefix}:{key}" if with_prefix else key

    def _remove_prefix(self, key: Optional[str]) -> str:
        p = f"{self.prefix}:"
        return key[len(p):] if isinstance(key, str) and key.startswith(p) else key

    def extend(self, states: Any):
        # Delegate to a local StateTape then merge
        local_tape = StateTape(states, with_prefix=False)
        for k, nodes in local_tape.states.items():
            self.states.setdefault(k, [])
            self.states[k].extend(nodes)

    def refresh(self):
        # Delegate to a fresh StateTape
        return StateTape({key: [] for key in self.states.keys()}, with_prefix=False).set_type(self._type)

    def revert(self) -> Union[list[Node], dict[str, list[Node]], None]:
        # SINGLE or MANY → flatten to a single list
        if self._type in (TapeType.SINGLE, TapeType.MANY):
            out_list: list[Node] = []
            for seq in self.states.values():
                out_list.extend(seq)
            return out_list

        # Any INDEX type → strip prefixes, return dict
        if self._type in (TapeType.INDEX_SINGLE, TapeType.INDEX_MANY):
            out_dict: dict[str, list[Node]] = {}
            for pk, seq in self.states.items():
                key = self._remove_prefix(pk)
                out_dict.setdefault(key, [])
                out_dict[key].extend(seq)
            return out_dict

        return None

    def _nodeify(self, x: Union[str, Node]) -> Node:
        # wrap raw strings into Node
        return x if isinstance(x, Node) else Node(x)
    
    def collect_activations(
        self,
        receiver_index: dict[str, Receiver],
        parsed_routes: dict[str, ParsedRoute],
    ) -> dict[tuple[int, ...], list[TapeActivation]]:
        """
        For each receiver, and each (key, state) in self.states,
        if the parsed route matches that state (or has no source),
        record a TapeActivation(priority, key, state, route, fn).
        """
        activation_index: dict[tuple[int, ...], list[TapeActivation]] = defaultdict(list)

        for route, receiver in receiver_index.items():
            parsed_route = parsed_routes.get(route)
            if parsed_route is None:
                continue

            # no gates → always fire once
            if parsed_route.is_initial:
                activation_index[receiver.priority].append(
                    TapeActivation(None, None, parsed_route, receiver.fn)
                )
                continue

            # otherwise test each gate against each state
            for key, states in self.states.items():
                for state in states:
                    for gate in parsed_route.source:
                        if gate.accepts(state):
                            activation_index[receiver.priority].append(
                                TapeActivation(key, state, parsed_route, receiver.fn)
                            )

        return dict(activation_index)

# ======= LIFE CYCLES =======

from enum import Enum, auto

class ClientIntent(Enum):
    QUIT      = auto()   # brutal, immediate exit
    TRAVEL    = auto()   # switch to a new host/port
    ABORT  = auto()   # abort due to error

