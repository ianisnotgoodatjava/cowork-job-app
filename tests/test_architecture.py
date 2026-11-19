"""The project's shape, checked rather than believed.

A layered design decays the moment one import points the wrong way, and that
import never looks wrong on its own — it is one line that makes something
work. So the layering is asserted here instead of described in a document
nobody re-reads.

Before this existed the graph was genuinely knotted: sources imported
sheetops for two string functions and sheetops imported sources to find job
links, so no ordering of the layers could be free of upward imports.
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import architecture

WHERE = {m: layer for layer, mods in architecture.LAYOUT.items() for m in mods}
RANK = {layer: i for i, layer in enumerate(architecture.LAYERS)}


def _local_imports(path):
    """Every project module a file imports, however the import is spelled.

    Three forms reach the same module and all three must be seen. The one
    that matters most is `from outreach import draft_writer`, because that is
    what the move produced everywhere -- reading only the module part of it
    yields "outreach", a layer name that is in no layer, so every violation
    slipped through and this test passed while the rule was being broken.
    """
    for node in ast.walk(ast.parse(open(path).read())):
        if isinstance(node, ast.Import):
            for alias in node.names:                   # import core.company_id
                yield alias.name.split(".")[-1]
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] in RANK:
                if len(parts) > 1:
                    yield parts[-1]                    # from core.company_id import key
                else:
                    for alias in node.names:           # from core import company_id
                        yield alias.name
            else:
                yield parts[0]


def _modules():
    for layer in architecture.LAYERS:
        folder = os.path.join(ROOT, layer)
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py") and name != "__init__.py":
                yield layer, name[:-3], os.path.join(folder, name)


class TestLayering:
    def test_no_module_imports_from_a_higher_layer(self):
        bad = []
        for layer, module, path in _modules():
            for dep in _local_imports(path):
                if dep in WHERE and RANK[WHERE[dep]] > RANK[layer]:
                    bad.append(f"{layer}/{module} imports {WHERE[dep]}/{dep}")
        assert not bad, "imports pointing upward:\n  " + "\n  ".join(bad)

    def test_core_touches_nothing_outside_core(self):
        # core is the one layer with no excuse: pure logic, no Gmail, no
        # sheet, no network. If it needs something from above, the something
        # is in the wrong place.
        bad = []
        for layer, module, path in _modules():
            if layer != "core":
                continue
            for dep in _local_imports(path):
                if dep in WHERE and WHERE[dep] != "core":
                    bad.append(f"core/{module} imports {WHERE[dep]}/{dep}")
        assert not bad, "core reaching outside itself:\n  " + "\n  ".join(bad)

    def test_every_module_on_disk_has_a_declared_layer(self):
        # A module nobody placed is a module nobody is checking.
        undeclared = [f"{layer}/{m}" for layer, m, _ in _modules()
                      if m not in WHERE]
        assert not undeclared, f"not in architecture.LAYOUT: {undeclared}"

    def test_every_declared_module_exists(self):
        on_disk = {m for _, m, _ in _modules()}
        missing = sorted(set(WHERE) - on_disk)
        assert not missing, f"declared but not on disk: {missing}"
