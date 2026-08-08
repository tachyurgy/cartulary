"""Two invariants: shadowed names never merge, and cycles never hang."""
from __future__ import annotations

import pytest

from cartulary.graph import ResolutionError, build, impact

SHADOW = {
    "billing.utils": "def parse(x):\n    return x\n",
    "ingest.utils": "def parse(x):\n    return x\n",
    "billing.charge": (
        "from billing.utils import parse\n"
        "def charge(row):\n"
        "    return parse(row)\n"
    ),
    "ingest.load": (
        "from ingest.utils import parse\n"
        "def load(row):\n"
        "    return parse(row)\n"
    ),
}

CYCLE = {
    "a": "import b\ndef f():\n    return b.g()\n",
    "b": "import c\ndef g():\n    return c.h()\n",
    "c": "import a\ndef h():\n    return a.f()\n",
}

CHAIN = {
    "core": "def base():\n    return 1\n",
    "mid": "from core import base\ndef middle():\n    return base()\n",
    "top": "from mid import middle\ndef top():\n    return middle()\n",
    "unrelated": "def alone():\n    return 0\n",
}


# -- shadowing (invariant one) --------------------------------------------


def test_shadowed_names_are_distinct_symbols():
    g = build(SHADOW)
    assert "billing.utils.parse" in g.symbols
    assert "ingest.utils.parse" in g.symbols


def test_the_collision_is_visible():
    g = build(SHADOW)
    assert g.short_name_collisions()["parse"] == ["billing.utils.parse", "ingest.utils.parse"]


def test_a_caller_is_attributed_to_the_right_parse():
    """THE invariant. Keying on the short name merges these two and reports
    each caller against both."""
    g = build(SHADOW)
    assert g.callers["billing.utils.parse"] == {"billing.charge.charge"}
    assert g.callers["ingest.utils.parse"] == {"ingest.load.load"}


def test_impact_of_one_parse_excludes_the_other_module():
    g = build(SHADOW)
    result = impact(g, "billing.utils.parse")
    assert result.direct == ["billing.charge.charge"]
    assert "ingest.load.load" not in result.transitive


def test_an_aliased_import_still_resolves_correctly():
    g = build(
        {
            "billing.utils": "def parse(x):\n    return x\n",
            "ingest.utils": "def parse(x):\n    return x\n",
            "app": (
                "from billing.utils import parse as bparse\n"
                "def run(r):\n"
                "    return bparse(r)\n"
            ),
        }
    )
    assert g.callers["billing.utils.parse"] == {"app.run"}
    assert g.callers["ingest.utils.parse"] == set()


def test_module_alias_resolves():
    g = build(
        {
            "billing.utils": "def parse(x):\n    return x\n",
            "app": "import billing.utils as bu\ndef run(r):\n    return bu.parse(r)\n",
        }
    )
    assert "app.run" in g.callers["billing.utils.parse"]


# -- cycles (invariant two) -----------------------------------------------


def test_a_cycle_terminates():
    """Naive recursion over reverse edges never returns on this input."""
    g = build(CYCLE)
    result = impact(g, "a.f")
    assert set(result.transitive) == {"b.g", "c.h"}


def test_cycle_membership_is_reported():
    g = build(CYCLE)
    result = impact(g, "a.f")
    assert set(result.in_cycle) == {"b.g", "c.h"}


def test_a_self_call_terminates():
    g = build({"m": "def f(n):\n    return f(n - 1)\n"})
    assert impact(g, "m.f").transitive == []


def test_a_two_node_cycle_terminates():
    g = build({"m": "def f():\n    return g()\ndef g():\n    return f()\n"})
    result = impact(g, "m.f")
    assert result.transitive == ["m.g"]
    assert result.in_cycle == ["m.g"]


# -- transitive completeness ----------------------------------------------


def test_transitive_impact_is_complete():
    g = build(CHAIN)
    result = impact(g, "core.base")
    assert result.transitive == ["mid.middle", "top.top"]
    assert result.direct == ["mid.middle"]


def test_depth_is_recorded():
    g = build(CHAIN)
    d = impact(g, "core.base").depth
    assert d["mid.middle"] == 1
    assert d["top.top"] == 2


def test_unrelated_code_is_not_impacted():
    g = build(CHAIN)
    assert "unrelated.alone" not in impact(g, "core.base").transitive


def test_forward_only_edges_do_not_create_impact():
    """Calling something does not mean you are affected by changes to your
    caller. Direction matters."""
    g = build(CHAIN)
    assert impact(g, "top.top").transitive == []


# -- resolution and hygiene -----------------------------------------------


def test_methods_are_qualified_by_class():
    g = build({"m": "class A:\n    def go(self):\n        return 1\n"})
    assert g.symbols["m.A.go"].kind == "method"


def test_self_calls_resolve_within_the_class():
    g = build(
        {"m": "class A:\n    def go(self):\n        return self.helper()\n"
              "    def helper(self):\n        return 1\n"}
    )
    assert "m.A.go" in g.callers["m.A.helper"]


def test_forward_references_across_modules_resolve():
    """Two passes matter: `top` calls into `core` regardless of dict order."""
    g = build({"top": "from core import base\ndef t():\n    return base()\n",
               "core": "def base():\n    return 1\n"})
    assert g.callers["core.base"] == {"top.t"}


def test_unresolved_references_are_kept_not_discarded():
    g = build({"m": "import json\ndef f():\n    return json.loads('{}')\n"})
    # json is not in the analysed source set; the edge is recorded against the
    # external fqn rather than dropped, so nothing is silently lost.
    assert "json.loads" in g.calls["m.f"] or g.unresolved


def test_unknown_symbol_raises():
    g = build(CHAIN)
    with pytest.raises(ResolutionError):
        impact(g, "nope.nope")


def test_a_syntax_error_names_its_module():
    with pytest.raises(ResolutionError, match="broken"):
        build({"broken": "def (:\n"})


def test_dotted_import_binds_only_the_root_name():
    """`import a.b` binds `a`; the call site spells out the rest. Binding the
    root to the dotted path yields `a.b.b.c` and loses the edge."""
    g = build(
        {
            "pkg.one": "def go():\n    return 1\n",
            "caller": "import pkg.one\ndef run():\n    return pkg.one.go()\n",
        }
    )
    assert g.callers["pkg.one.go"] == {"caller.run"}


def test_dotted_import_cycle_is_detected():
    from cartulary.corpus import CORPUS

    g = build(CORPUS)
    result = impact(g, "legacy.alpha.start")
    assert set(result.in_cycle) == {"legacy.beta.middle", "legacy.gamma.finish"}
