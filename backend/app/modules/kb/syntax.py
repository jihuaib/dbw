"""CLI syntax grammar and deterministic command matching.

Manuals describe one command with a small grammar rather than a plain prefix::

    display bgp [ instance <name> ] peer { ipv4 | ipv6 } <address>

The importer uses :func:`analyze` to find a safe minimal invocation and required
parameters.  The planner uses :func:`match` to validate a concrete command
against the *whole* grammar.  Keeping both operations here prevents the parser
and the command gate from slowly developing different interpretations.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import ipaddress
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


# A parameter is still one shell-free CLI token.  Comma and equals are needed
# by route keys such as ``dqpn=1,ip=10.0.0.0/24``.
SAFE_TOKEN_PATTERN = r"[A-Za-z0-9_.:/,=+%@~\-]+"
SAFE_TOKEN_RE = re.compile(SAFE_TOKEN_PATTERN)
INPUT_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:/,=+%@~\-\[\]]+")
PARAM_RE = re.compile(r"<([^<>]+)>")
REPEAT_PARAM_RE = re.compile(r"^(<[^<>]+>)&<(\d+)(?:-(\d+))?>$")
_BANNED_INPUT = set("|;&`$<>{}\\\"'")
MAX_NESTING = 64
MAX_NODES = 1024


Node = Tuple[Any, ...]


@dataclass(frozen=True)
class SyntaxMatch:
    command: str
    literal_count: int
    parameter_count: int


@dataclass(frozen=True)
class _Path:
    # ("lit" | "param", rendered token/template)
    tokens: Tuple[Tuple[str, str], ...] = ()
    required: Tuple[str, ...] = ()
    parameter_count: int = 0


def safe_command_text(value: str) -> bool:
    """Reject control characters and command separators before whitespace folding."""
    if not isinstance(value, str) or not value.strip():
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    return not any(ch in _BANNED_INPUT for ch in value)


def _lex(source: str) -> Tuple[str, ...]:
    """Split grammar punctuation, keeping ``|`` inside ``<param|param>``."""
    out: List[str] = []
    buf: List[str] = []
    in_angle = False

    def flush() -> None:
        if buf:
            out.append("".join(buf))
            buf.clear()

    for ch in source.strip():
        if ch == "<":
            if in_angle:
                raise ValueError("nested '<' in CLI syntax")
            in_angle = True
            buf.append(ch)
        elif ch == ">":
            if not in_angle:
                raise ValueError("unmatched '>' in CLI syntax")
            in_angle = False
            buf.append(ch)
        elif not in_angle and ch in "[]{}|":
            flush()
            out.append(ch)
        elif not in_angle and ch.isspace():
            flush()
        else:
            buf.append(ch)
    if in_angle:
        raise ValueError("unclosed '<' in CLI syntax")
    flush()
    return tuple(out)


class _Parser:
    def __init__(self, tokens: Tuple[str, ...]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.nodes = 0

    def parse(self) -> Node:
        node = self._expr("", 0)
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token in CLI syntax: {0}".format(
                self.tokens[self.pos]))
        return node

    def _expr(self, stop: str, depth: int) -> Node:
        branches = [self._seq(stop, depth)]
        while self.pos < len(self.tokens) and self.tokens[self.pos] == "|":
            self.pos += 1
            branches.append(self._seq(stop, depth))
        if any(branch == ("seq", ()) for branch in branches):
            raise ValueError("empty alternative in CLI syntax")
        if len(branches) == 1:
            return branches[0]
        return ("alt", tuple(branches))

    def _seq(self, stop: str, depth: int) -> Node:
        children: List[Node] = []
        while self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            if tok == "|" or (stop and tok == stop):
                break
            if tok in ("]", "}"):
                raise ValueError("unmatched '{0}' in CLI syntax".format(tok))
            self.pos += 1
            self.nodes += 1
            if self.nodes > MAX_NODES:
                raise ValueError("too many nodes in CLI syntax")
            if tok == "[":
                if depth >= MAX_NESTING:
                    raise ValueError("CLI syntax nesting is too deep")
                inner = self._expr("]", depth + 1)
                if self.pos >= len(self.tokens) or self.tokens[self.pos] != "]":
                    raise ValueError("unclosed '[' in CLI syntax")
                self.pos += 1
                children.append(("optional", inner))
            elif tok == "{":
                if depth >= MAX_NESTING:
                    raise ValueError("CLI syntax nesting is too deep")
                inner = self._expr("}", depth + 1)
                if self.pos >= len(self.tokens) or self.tokens[self.pos] != "}":
                    raise ValueError("unclosed '{' in CLI syntax")
                self.pos += 1
                children.append(("choice", inner))
            elif tok == "...":
                # Ellipsis documents omitted detail; it is never an open-ended
                # authorization for arbitrary device input.
                children.append(("empty",))
            elif REPEAT_PARAM_RE.fullmatch(tok):
                repeat = REPEAT_PARAM_RE.fullmatch(tok)
                lower = int(repeat.group(2))
                upper = int(repeat.group(3) or repeat.group(2))
                name = PARAM_RE.fullmatch(repeat.group(1)).group(1)
                if not name.strip() or any(ch.isspace() for ch in name) \
                        or lower < 1 or upper < lower or upper > 64:
                    raise ValueError("invalid repeated parameter in CLI syntax")
                children.append(
                    ("repeat", ("param", repeat.group(1)), lower, upper))
            elif PARAM_RE.search(tok):
                names = PARAM_RE.findall(tok)
                if any(not name.strip() or any(ch.isspace() for ch in name)
                       for name in names):
                    raise ValueError("invalid parameter name in CLI syntax")
                children.append(("param", tok))
            else:
                children.append(("literal", tok))
        return ("seq", tuple(children))


@lru_cache(maxsize=4096)
def parse(source: str) -> Node:
    tokens = _lex(" ".join(str(source).split()))
    if not tokens:
        raise ValueError("empty CLI syntax")
    return _Parser(tokens).parse()


def _path_rank(path: _Path) -> Tuple[Any, ...]:
    rendered = tuple(value.lower() for _kind, value in path.tokens)
    return (path.parameter_count, len(path.tokens), rendered)


def _minimal(node: Node) -> _Path:
    kind = node[0]
    if kind in ("empty",):
        return _Path()
    if kind == "literal":
        return _Path(tokens=(("lit", node[1]),))
    if kind == "param":
        names = tuple(x.strip() for x in PARAM_RE.findall(node[1]) if x.strip())
        return _Path(tokens=(("param", node[1]),), required=names,
                     parameter_count=1)
    if kind == "repeat":
        child = _minimal(node[1])
        return _Path(tokens=child.tokens * node[2],
                     required=child.required * node[2],
                     parameter_count=child.parameter_count * node[2])
    if kind == "optional":
        return _Path()
    if kind == "choice":
        return _minimal(node[1])
    if kind == "alt":
        paths = [_minimal(branch) for branch in node[1]]
        return min(paths, key=_path_rank) if paths else _Path()
    if kind == "seq":
        tokens: List[Tuple[str, str]] = []
        required: List[str] = []
        count = 0
        for child in node[1]:
            got = _minimal(child)
            tokens.extend(got.tokens)
            required.extend(got.required)
            count += got.parameter_count
        return _Path(tuple(tokens), tuple(required), count)
    raise ValueError("unknown CLI syntax node: {0}".format(kind))


def _all_parameters(node: Node) -> Iterable[str]:
    kind = node[0]
    if kind == "param":
        for name in PARAM_RE.findall(node[1]):
            if name.strip():
                yield name.strip()
    elif kind == "repeat":
        yield from _all_parameters(node[1])
    elif kind in ("seq", "alt"):
        for child in node[1]:
            yield from _all_parameters(child)
    elif kind in ("optional", "choice"):
        yield from _all_parameters(node[1])


def _all_literals(node: Node) -> Iterable[str]:
    kind = node[0]
    if kind == "literal":
        yield node[1]
    elif kind == "repeat":
        yield from _all_literals(node[1])
    elif kind in ("seq", "alt"):
        for child in node[1]:
            yield from _all_literals(child)
    elif kind in ("optional", "choice"):
        yield from _all_literals(node[1])


def _first_symbols(node: Node) -> Tuple[set, bool]:
    """Return possible first literals (None means parameter) and nullability."""
    kind = node[0]
    if kind == "empty":
        return set(), True
    if kind == "literal":
        return {node[1].lower()}, False
    if kind == "param":
        return {None}, False
    if kind == "repeat":
        symbols, nullable = _first_symbols(node[1])
        return symbols, nullable if node[2] else True
    if kind == "optional":
        symbols, _nullable = _first_symbols(node[1])
        return symbols, True
    if kind in ("choice", "alt"):
        children = (node[1],) if kind == "choice" else node[1]
        symbols: set = set()
        nullable = False
        for child in children:
            child_symbols, child_nullable = _first_symbols(child)
            symbols.update(child_symbols)
            nullable = nullable or child_nullable
        return symbols, nullable
    if kind == "seq":
        symbols: set = set()
        nullable = True
        for child in node[1]:
            if not nullable:
                break
            child_symbols, child_nullable = _first_symbols(child)
            symbols.update(child_symbols)
            nullable = nullable and child_nullable
        return symbols, nullable
    raise ValueError("unknown CLI syntax node: {0}".format(kind))


def starts_with_one_of(source: str, allowed: Iterable[str]) -> bool:
    """Every grammar path must start with one of the allowed literal verbs."""
    symbols, nullable = _first_symbols(parse(source))
    accepted = {str(word).lower() for word in allowed}
    return bool(symbols) and not nullable and None not in symbols \
        and all(symbol in accepted for symbol in symbols)


def analyze(source: str) -> Dict[str, Any]:
    """Return a minimal concrete command/prefix and grammar metadata.

    If the cheapest valid branch contains no parameter, ``command`` is a
    complete command suitable for capability probing.  Otherwise it is the
    fixed prefix before the first required parameter and ``required`` is
    non-empty, so callers know not to send the prefix by itself.
    """
    node = parse(source)
    path = _minimal(node)
    if path.parameter_count:
        rendered: List[str] = []
        for token_kind, value in path.tokens:
            if token_kind == "param":
                break
            rendered.append(value)
    else:
        rendered = [value for _kind, value in path.tokens]
    required = list(dict.fromkeys(path.required))
    params = list(dict.fromkeys(_all_parameters(node)))
    literals = list(dict.fromkeys(_all_literals(node)))
    return {"command": " ".join(rendered), "base": " ".join(rendered),
            "required": required, "params": params, "literals": literals,
            "runnable": not path.parameter_count}


def _semantic_parameter(name: str, value: str) -> bool:
    options = {x.strip().lower() for x in name.split("|") if x.strip()}
    address_kinds = {"ipv4-address", "ipv6-address", "ip-address", "ip"}
    if options and options <= address_kinds:
        try:
            version = ipaddress.ip_address(value).version
        except ValueError:
            return False
        return any(kind in options for kind in (
            "ip-address", "ip", "ipv{0}-address".format(version)))
    if options and options <= {"mask-length", "prefix-length", "masklen"}:
        return value.isdigit() and 0 <= int(value) <= 128
    if options == {"mask"}:
        try:
            return ipaddress.ip_address(value).version == 4
        except ValueError:
            return False
    return True


def _ls_prefix_matches(value: str) -> bool:
    """Validate Huawei's bracket-encoded BGP-LS NLRI as one bounded token."""
    if len(value) > 2048 or not INPUT_TOKEN_RE.fullmatch(value):
        return False
    got = re.fullmatch(r"(\[.*\])(?:/(\d{1,4}))?", value)
    if not got or (got.group(2) and int(got.group(2)) > 4096):
        return False
    depth = 0
    for ch in got.group(1):
        if ch == "[":
            depth += 1
            if depth > 32:
                return False
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0:
            return False
    return depth == 0


def _parameter_matches(template: str, value: str) -> bool:
    matches = list(PARAM_RE.finditer(template))
    if not matches:
        return False
    if len(matches) == 1 and matches[0].span() == (0, len(template)) \
            and matches[0].group(1).strip().lower() == "ls-prefix":
        return _ls_prefix_matches(value)
    if not SAFE_TOKEN_RE.fullmatch(value):
        return False
    pattern: List[str] = []
    names: List[str] = []
    end = 0
    for item in matches:
        pattern.append(re.escape(template[end:item.start()]))
        pattern.append("(" + SAFE_TOKEN_PATTERN + ")")
        names.append(item.group(1).strip())
        end = item.end()
    pattern.append(re.escape(template[end:]))
    got = re.fullmatch("".join(pattern), value)
    if not got:
        return False
    return all(_semantic_parameter(name, part)
               for name, part in zip(names, got.groups()))


def _candidate_rank(item: Tuple[Tuple[str, ...], int, int]) -> Tuple[Any, ...]:
    output, literal_count, parameter_count = item
    return (-literal_count, parameter_count,
            tuple(token.lower() for token in output))


def match(source: str, concrete: str) -> Optional[SyntaxMatch]:
    """Match a concrete command against ``source`` and consume it completely."""
    if not safe_command_text(concrete):
        return None
    words = tuple(concrete.strip().split())
    if not words or any(not INPUT_TOKEN_RE.fullmatch(word) for word in words):
        return None
    try:
        root = parse(source)
    except ValueError:
        return None

    @lru_cache(maxsize=None)
    def run(node: Node, pos: int) -> Tuple[Tuple[int, Tuple[str, ...], int, int], ...]:
        kind = node[0]
        if kind == "empty":
            return ((pos, (), 0, 0),)
        if kind == "literal":
            if pos < len(words) and words[pos].lower() == node[1].lower():
                return ((pos + 1, (node[1],), 1, 0),)
            return ()
        if kind == "param":
            if pos < len(words) and _parameter_matches(node[1], words[pos]):
                return ((pos + 1, (words[pos],), 0, 1),)
            return ()
        if kind == "repeat":
            states: Dict[int, Tuple[Tuple[str, ...], int, int]] = {
                pos: ((), 0, 0)}
            completed: Dict[int, Tuple[Tuple[str, ...], int, int]] = {}
            for count in range(1, node[3] + 1):
                following: Dict[int, Tuple[Tuple[str, ...], int, int]] = {}
                for start, (prefix, literals, params) in states.items():
                    for end, output, child_literals, child_params in run(
                            node[1], start):
                        if end == start:
                            continue
                        candidate = (prefix + output, literals + child_literals,
                                     params + child_params)
                        previous = following.get(end)
                        if previous is None or \
                                _candidate_rank(candidate) < _candidate_rank(previous):
                            following[end] = candidate
                states = following
                if not states:
                    break
                if count >= node[2]:
                    for end, candidate in states.items():
                        previous = completed.get(end)
                        if previous is None or \
                                _candidate_rank(candidate) < _candidate_rank(previous):
                            completed[end] = candidate
            return tuple((end, value[0], value[1], value[2])
                         for end, value in sorted(completed.items()))
        if kind == "optional":
            choices = [(pos, (), 0, 0)] + list(run(node[1], pos))
        elif kind == "choice":
            choices = list(run(node[1], pos))
        elif kind == "alt":
            choices = []
            for branch in node[1]:
                choices.extend(run(branch, pos))
        elif kind == "seq":
            states: Dict[int, Tuple[Tuple[str, ...], int, int]] = {
                pos: ((), 0, 0)}
            for child in node[1]:
                following: Dict[int, Tuple[Tuple[str, ...], int, int]] = {}
                for start, (prefix, literals, params) in states.items():
                    for end, output, child_literals, child_params in run(child, start):
                        candidate = (prefix + output, literals + child_literals,
                                     params + child_params)
                        previous = following.get(end)
                        if previous is None or _candidate_rank(candidate) < _candidate_rank(previous):
                            following[end] = candidate
                states = following
                if not states:
                    break
            return tuple((end, value[0], value[1], value[2])
                         for end, value in sorted(states.items()))
        else:
            return ()

        best: Dict[int, Tuple[Tuple[str, ...], int, int]] = {}
        for end, output, literals, params in choices:
            candidate = (output, literals, params)
            previous = best.get(end)
            if previous is None or _candidate_rank(candidate) < _candidate_rank(previous):
                best[end] = candidate
        return tuple((end, value[0], value[1], value[2])
                     for end, value in sorted(best.items()))

    complete = [(output, literals, params)
                for end, output, literals, params in run(root, 0)
                if end == len(words)]
    if not complete:
        return None
    output, literals, params = min(complete, key=_candidate_rank)
    return SyntaxMatch(" ".join(output), literals, params)
