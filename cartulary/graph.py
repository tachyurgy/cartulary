"""A symbol graph for reasoning about what a change breaks.

The question a tool like this exists to answer is "if I change this, what else
has to be looked at". Getting it wrong in the safe direction produces noise;
getting it wrong in the unsafe direction means a caller was missed, and on a
codebase nobody holds in their head, a missed caller is a production incident.

Two things make that hard in a real repository, and both are handled here rather
than assumed away:

**Shadowing.** The same short name means different things in different modules.
`billing.utils.parse` and `ingest.utils.parse` are unrelated functions, and a
graph keyed on the short name silently merges them -- which both invents impact
that does not exist and, worse, makes a genuine caller of one look like it was
already accounted for by the other. Every symbol here is keyed by its fully
qualified name, and a reference is resolved through the referring module's own
import bindings before anything else.

**Cycles.** Import cycles are common in code of this age. A naive transitive
walk recurses forever; a walk that guards with a "visited" set gets the right
answer only if the guard is checked before recursion rather than after. Impact
analysis here is iterative and returns the cycle members it found, since a cycle
is itself something the caller usually wants to know about.
"""
from __future__ import annotations

import ast
from collections import defaultdict, deque
from dataclasses import dataclass, field


class ResolutionError(Exception):
    pass


@dataclass(frozen=True)
class Symbol:
    """A definition, keyed by fully qualified name."""

    fqn: str
    module: str
    name: str
    kind: str  # "function" | "class" | "method"
    lineno: int


@dataclass
class Graph:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    #: caller fqn -> set of callee fqns
    calls: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: callee fqn -> set of caller fqns
    callers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: module -> {local name: fully qualified name}
    bindings: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    #: references that could not be resolved, kept rather than discarded
    unresolved: list[tuple[str, str]] = field(default_factory=list)

    def add_symbol(self, sym: Symbol) -> None:
        self.symbols[sym.fqn] = sym

    def add_call(self, caller: str, callee: str) -> None:
        self.calls[caller].add(callee)
        self.callers[callee].add(caller)

    def short_name_collisions(self) -> dict[str, list[str]]:
        """Short names that are defined in more than one module.

        Exposed because it is the evidence that fully qualified keying is doing
        something: on a real codebase this list is never empty.
        """
        by_short: dict[str, list[str]] = defaultdict(list)
        for sym in self.symbols.values():
            by_short[sym.name].append(sym.fqn)
        return {k: sorted(v) for k, v in by_short.items() if len(v) > 1}


class _ModuleVisitor(ast.NodeVisitor):
    """Collects definitions, import bindings and call edges for one module."""

    def __init__(self, graph: Graph, module: str) -> None:
        self.g = graph
        self.module = module
        self.scope: list[str] = []

    # -- imports ----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                # `import a.b as x` binds x to the full dotted module.
                self.g.bindings[self.module][alias.asname] = alias.name
            else:
                # `import a.b` binds only the ROOT name `a`, and a later
                # `a.b.c()` is spelled out in full at the call site. Binding the
                # root to the dotted path instead produces `a.b.b.c`, which
                # resolves to nothing and quietly loses the edge.
                root = alias.name.split(".")[0]
                self.g.bindings[self.module][root] = root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.g.bindings[self.module][local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    # -- definitions ------------------------------------------------------

    def _define(self, node, kind: str) -> str:
        qual = ".".join([self.module, *self.scope, node.name])
        self.g.add_symbol(
            Symbol(fqn=qual, module=self.module, name=node.name, kind=kind, lineno=node.lineno)
        )
        # A definition binds its own short name inside its module.
        self.g.bindings[self.module].setdefault(node.name, qual)
        return qual

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._define(node, "class")
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.scope else "function"
        qual = self._define(node, kind)
        self.scope.append(node.name)
        for child in node.body:
            for call in ast.walk(child):
                if isinstance(call, ast.Call):
                    target = self._resolve(call.func)
                    if target:
                        self.g.add_call(qual, target)
                    else:
                        self.g.unresolved.append((qual, _render(call.func)))
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    # -- resolution -------------------------------------------------------

    def _resolve(self, func: ast.AST) -> str | None:
        """Turn a call expression into a fully qualified name.

        Resolution goes through this module's bindings first. That is the whole
        defence against shadowing: `parse` inside `billing.utils` resolves to
        whatever `billing.utils` imported or defined, never to a same-named
        function somewhere else in the repository.
        """
        binds = self.g.bindings[self.module]

        if isinstance(func, ast.Name):
            if func.id in binds:
                return binds[func.id]
            local = f"{self.module}.{func.id}"
            return local if local in self.g.symbols else None

        if isinstance(func, ast.Attribute):
            base = _render(func.value)
            if base is None:
                return None
            root = base.split(".")[0]
            if root in binds:
                prefix = binds[root]
                rest = base.split(".")[1:]
                return ".".join([prefix, *rest, func.attr])
            # `self.method()` inside a class resolves within the current class.
            if base == "self" and self.scope:
                return ".".join([self.module, self.scope[0], func.attr])
            return f"{base}.{func.attr}"

        return None


def _render(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _render(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def build(sources: dict[str, str]) -> Graph:
    """Build a graph from {module name: source}.

    Two passes. Definitions and bindings are collected for every module before
    any call edge is resolved, because a module that calls into one parsed later
    would otherwise resolve to nothing and be quietly filed as unresolved.
    """
    graph = Graph()

    for module, src in sources.items():
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            raise ResolutionError(f"{module}: {exc}") from exc
        collector = _ModuleVisitor(graph, module)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                collector.visit(node)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                pass
        # definitions, without call edges yet
        _DefOnly(graph, module).visit(tree)

    for module, src in sources.items():
        _ModuleVisitor(graph, module).visit(ast.parse(src))

    return graph


class _DefOnly(_ModuleVisitor):
    """First pass: definitions and bindings only, no call edges."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
        kind = "method" if self.scope else "function"
        self._define(node, kind)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


# -- impact ---------------------------------------------------------------


@dataclass
class Impact:
    target: str
    direct: list[str]
    transitive: list[str]
    in_cycle: list[str]
    depth: dict[str, int]


def impact(graph: Graph, target: str) -> Impact:
    """Everything that has to be looked at if `target` changes.

    Breadth-first over the reverse edges, iterative rather than recursive, with
    the visited check performed before enqueueing. A cycle therefore terminates,
    and its members are reported instead of being silently absorbed.
    """
    if target not in graph.symbols:
        raise ResolutionError(f"unknown symbol: {target}")

    direct = sorted(graph.callers.get(target, set()))
    seen: dict[str, int] = {target: 0}
    queue: deque[str] = deque([target])

    while queue:
        node = queue.popleft()
        for caller in graph.callers.get(node, set()):
            if caller in seen:
                continue
            seen[caller] = seen[node] + 1
            queue.append(caller)

    transitive = sorted(k for k in seen if k != target)

    # Cycle membership: a node that can reach the target and is reachable from it.
    forward: set[str] = set()
    stack = [target]
    while stack:
        node = stack.pop()
        for callee in graph.calls.get(node, set()):
            if callee not in forward:
                forward.add(callee)
                stack.append(callee)
    # The target is excluded, consistently with `transitive` and `depth`: the
    # caller already knows what they asked about, and reporting it back as its
    # own impact is noise.
    in_cycle = sorted((forward & set(seen)) - {target})

    return Impact(
        target=target,
        direct=direct,
        transitive=transitive,
        in_cycle=in_cycle,
        depth={k: v for k, v in sorted(seen.items()) if k != target},
    )
