"""The Desktop wire contract has one source: ``agent/service/wire_schema``.

These checks keep every copy derived from it honest — the generated
TypeScript file, the hand-written ``from_dict``/``to_dict`` of each
contract dataclass, and the dispatcher's routing table.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
import typing

from agent.service.local_protocol import LocalTransportMethod
from agent.service.local_sidecar.dispatcher import SidecarDispatcher
from agent.service.wire_codegen import TS_OUTPUT, render_typescript, to_dict_keys, wire_properties
from agent.service.wire_schema import WIRE_METHODS


def _dataclasses_in(tp: object, seen: set[type]) -> None:
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        if tp in seen:
            return
        seen.add(tp)
        for hint in typing.get_type_hints(tp).values():
            _dataclasses_in(hint, seen)
        return
    if isinstance(tp, type) and typing.is_typeddict(tp):
        for hint in typing.get_type_hints(tp).values():
            _dataclasses_in(hint, seen)
        return
    for arg in typing.get_args(tp):
        _dataclasses_in(arg, seen)


def _reachable(role: str) -> set[type]:
    seen: set[type] = set()
    for spec in WIRE_METHODS.values():
        _dataclasses_in(getattr(spec, role), seen)
    return seen


def _method_tree(cls: type, name: str) -> ast.FunctionDef | None:
    func = cls.__dict__.get(name)
    if func is None:
        return None
    func = func.__func__ if isinstance(func, classmethod) else func
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return typing.cast(ast.FunctionDef, tree.body[0])


def _from_dict_keys(cls: type) -> set[str]:
    keys: set[str] = set()
    for klass in cls.__mro__:
        tree = _method_tree(klass, "from_dict")
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                keys.add(node.args[0].value)
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                keys.add(node.slice.value)
    return keys


def _field_names(cls: type) -> set[str]:
    return {item.name for item in dataclasses.fields(cls)}


def test_every_transport_method_is_declared() -> None:
    assert set(WIRE_METHODS) == set(LocalTransportMethod)


def test_generated_typescript_is_current() -> None:
    assert TS_OUTPUT.read_text(encoding="utf-8") == render_typescript(), (
        "wire.generated.ts is stale — run: python scripts/gen_wire_types.py"
    )


def test_request_dataclasses_read_exactly_their_fields() -> None:
    mismatched = {
        cls.__name__: (_from_dict_keys(cls) ^ _field_names(cls))
        for cls in _reachable("params")
        if hasattr(cls, "from_dict") and _from_dict_keys(cls) != _field_names(cls)
    }
    assert not mismatched


def test_result_dataclasses_write_exactly_their_fields() -> None:
    missing_to_dict = [cls.__name__ for cls in _reachable("result") if not hasattr(cls, "to_dict")]
    assert not missing_to_dict, "the dispatcher only serializes objects with to_dict()"
    def declared(cls: type) -> set[str]:
        return _field_names(cls) | set(wire_properties(cls))

    mismatched = {
        cls.__name__: (to_dict_keys(cls) ^ declared(cls))
        for cls in _reachable("result")
        if to_dict_keys(cls) != declared(cls)
    }
    assert not mismatched


def test_dispatcher_routes_plain_methods_through_the_schema() -> None:
    for method, spec in WIRE_METHODS.items():
        has_handler = hasattr(SidecarDispatcher, f"_{method.value}")
        assert has_handler != bool(spec.service), method
