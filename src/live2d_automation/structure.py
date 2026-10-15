from __future__ import annotations

import copy
import math
from typing import Any

from .models import AutomationError, Operation


def parameter_index(tree: dict) -> tuple[dict, dict]:
    params, groups = {}, {}

    def walk(node, group="", root=False):
        if not isinstance(node, dict):
            raise AutomationError("INVALID_STRUCTURE", "Parameter tree contains a non-object")
        kind = node.get("EntryType")
        object_id = node.get("Id")
        if kind in {"Parameter", "ParameterGroup"}:
            if not object_id or object_id in params or object_id in groups:
                raise AutomationError(
                    "ID_CONFLICT", "Duplicate or empty parameter/group ID", id=object_id
                )
        if kind == "Parameter":
            values: list[Any] = [node.get(k) for k in ("Min", "Default", "Max")]
            if (
                not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in values
                )
                or not values[0] <= values[1] <= values[2]
            ):
                raise AutomationError("INVALID_STRUCTURE", "Invalid parameter range", id=object_id)
            keys = node.get("KeyValues", [])
            if (
                not isinstance(keys, list)
                or any(
                    not isinstance(v, (int, float))
                    or isinstance(v, bool)
                    or not math.isfinite(v)
                    or not values[0] <= v <= values[2]
                    for v in keys
                )
                or len(set(keys)) != len(keys)
            ):
                raise AutomationError(
                    "INVALID_STRUCTURE", "Invalid or duplicate parameter keys", id=object_id
                )
            entry = copy.deepcopy(node)
            # Alpha2's actual response uses Repeat/Type and omits KeyValues.
            # Keep absent keys absent until independently read from every object.
            if "Repeat" in entry:
                entry["IsRepeat"] = entry.pop("Repeat")
            if "Type" in entry:
                value = entry.pop("Type")
                if value not in (0, 1):
                    raise AutomationError("INVALID_STRUCTURE", "Unknown parameter type")
                entry["IsBlendShape"] = value == 1
            params[object_id] = {**entry, "GroupId": group}
        elif kind == "ParameterGroup":
            groups[object_id] = {
                k: copy.deepcopy(v) for k, v in node.items() if k not in {"Entries", "Parameters"}
            }
            if "GroupName" in groups[object_id]:
                groups[object_id]["Name"] = groups[object_id].pop("GroupName")
            children = node.get("Parameters", node.get("Entries", []))
            if not isinstance(children, list):
                raise AutomationError(
                    "INVALID_STRUCTURE", "Parameter group children must be a list"
                )
            for child in children:
                walk(child, object_id)
        elif root:
            children = node.get("Entries", [])
            if not isinstance(children, list):
                raise AutomationError("INVALID_STRUCTURE", "Parameter root entries must be a list")
            for child in children:
                walk(child)
        else:
            raise AutomationError("INVALID_STRUCTURE", "Unknown parameter entry type")

    walk(tree, root=True)
    return params, groups


def object_index(tree: dict | list) -> dict:
    objects = {}
    if isinstance(tree, list):
        if len(tree) != 1 or not isinstance(tree[0], dict) or tree[0].get("Id") != "%Root":
            raise AutomationError("INVALID_STRUCTURE", "Unknown deformer root list")
        tree = tree[0]

    def walk(node, parent="", root=False):
        if not isinstance(node, dict):
            raise AutomationError("INVALID_STRUCTURE", "Object tree contains a non-object")
        if not root:
            object_id = node.get("Id")
            if not object_id or object_id in objects:
                raise AutomationError(
                    "INVALID_STRUCTURE",
                    "Object hierarchy has a duplicate ID or cycle",
                    id=object_id,
                )
            objects[object_id] = {
                "Id": object_id,
                "Name": node.get("Name"),
                "Type": node.get("Type"),
                "TreeParentId": parent,
            }
            parent = object_id
        children = node.get("Children", node.get("Entries", []))
        if not isinstance(children, list):
            raise AutomationError("INVALID_STRUCTURE", "Object children must be a list")
        for child in children:
            walk(child, parent)

    walk(tree, root=True)
    return objects


def normalize(parameter_tree, part_tree, deformer_tree):
    params, groups = parameter_index(parameter_tree)
    return {
        "parameters": params,
        "groups": groups,
        "parts": object_index(part_tree),
        "deformers": object_index(deformer_tree),
    }


METHODS = {
    "create_parameter": "AddParameter",
    "update_parameter": "EditParameter",
    "delete_parameter": "DeleteParameter",
    "move_parameter": "MoveParameter",
    "create_group": "AddParameterGroup",
    "update_group": "EditParameterGroup",
    "delete_group": "DeleteParameterGroup",
    "create_part": "AddPart",
    "create_warp": "AddWarpDeformer",
    "create_rotation": "AddRotationDeformer",
}
FIELDS = {
    "id": "Id",
    "name": "Name",
    "min": "Min",
    "default": "Default",
    "max": "Max",
    "group_id": "GroupId",
    "parent_id": "ParentId",
    "warp_div_h": "WarpDivH",
    "warp_div_v": "WarpDivV",
    "bezier_div_h": "BezierDivH",
    "bezier_div_v": "BezierDivV",
}


def plan_operations(snapshot: dict, operations: list[Operation]) -> dict:
    desired = copy.deepcopy(snapshot["normalized"])
    requests, differences, object_checks = [], [], []
    for op in operations:
        is_param = op.kind.endswith("parameter")
        is_group = op.kind.endswith("group")
        bucket = "parameters" if is_param else "groups" if is_group else "parts"
        table = desired[bucket]
        old = copy.deepcopy(table.get(op.id))
        fields = {FIELDS[k]: v for k, v in op.model_dump(exclude_none=True).items() if k in FIELDS}
        if op.kind.startswith("create_"):
            if op.name is None:
                fields["Name"] = op.id
            if is_param or is_group:
                other = desired["groups" if is_param else "parameters"]
                if op.id in other:
                    raise AutomationError(
                        "ID_CONFLICT", "ID already used by another parameter entry", id=op.id
                    )
            if old:
                if not is_param and not is_group:
                    raise AutomationError(
                        "ID_CONFLICT", "Object ID already exists; inspect it before reuse", id=op.id
                    )
                wanted = {k: v for k, v in fields.items()}
                if is_param:
                    wanted["GroupId"] = op.group_id or ""
                if all(old.get(k) == v for k, v in wanted.items()):
                    continue
                raise AutomationError(
                    "ID_CONFLICT",
                    "Existing entry differs from requested creation",
                    id=op.id,
                    existing=old,
                    requested=wanted,
                )
            if is_param:
                if op.group_id and op.group_id not in desired["groups"]:
                    raise AutomationError(
                        "MISSING_PARENT", "Parameter group does not exist", id=op.group_id
                    )
                table[op.id] = {
                    **fields,
                    "GroupId": op.group_id or "",
                    "EntryType": "Parameter",
                    "IsRepeat": False,
                    "IsBlendShape": False,
                    "KeyValues": [],
                }
            elif is_group:
                table[op.id] = {**fields, "EntryType": "ParameterGroup"}
            else:
                # MVP creates empty containers only; automatic child reparenting needs
                # more version-specific topology evidence than an ID existence check.
                if op.parent_id and (
                    op.parent_id not in desired["parts"]
                    or desired["parts"][op.parent_id]["Type"] != "Part"
                ):
                    raise AutomationError(
                        "MISSING_PARENT", "Parent part does not exist", id=op.parent_id
                    )
                kind = {
                    "create_part": "Part",
                    "create_warp": "WarpDeformer",
                    "create_rotation": "RotationDeformer",
                }[op.kind]
                table[op.id] = {
                    "Id": op.id,
                    "Name": fields["Name"],
                    "Type": kind,
                    "TreeParentId": op.parent_id or "",
                }
                if kind != "Part":
                    desired["deformers"][op.id] = {
                        "Id": op.id,
                        "Name": fields["Name"],
                        "Type": kind,
                        "TreeParentId": "",
                    }
                object_checks.append(
                    {
                        "Id": op.id,
                        "Type": kind,
                        "fields": {**fields, "ParentId": op.parent_id or ""},
                    }
                )
        else:
            if old is None:
                raise AutomationError("MISSING_ID", "Target ID does not exist", id=op.id)
            if is_param and "KeyValues" not in old:
                raise AutomationError(
                    "BINDINGS_UNKNOWN", "Read object parameter bindings before editing"
                )
            if op.kind.startswith("delete_"):
                if is_group and any(p["GroupId"] == op.id for p in desired["parameters"].values()):
                    raise AutomationError(
                        "NONEMPTY_GROUP", "Move parameters before deleting their group", id=op.id
                    )
                if is_param and old.get("KeyValues"):
                    raise AutomationError(
                        "BOUND_PARAMETER",
                        "Deleting a parameter with keyforms is outside this MVP",
                        id=op.id,
                    )
                table.pop(op.id)
            else:
                if (
                    op.kind == "move_parameter"
                    and op.group_id
                    and op.group_id not in desired["groups"]
                ):
                    raise AutomationError("MISSING_PARENT", "Target group does not exist")
                table[op.id].update(fields)
                if table[op.id] == old:
                    continue
                if is_param:
                    p = table[op.id]
                    if not p["Min"] <= p["Default"] <= p["Max"] or any(
                        not p["Min"] <= k <= p["Max"] for k in p["KeyValues"]
                    ):
                        raise AutomationError(
                            "INVALID_RANGE",
                            "New range excludes the default or existing keys",
                            id=op.id,
                        )
        wire_fields = copy.deepcopy(fields)
        if op.kind == "move_parameter":
            if not op.group_id:
                root_id = snapshot.get("raw", {}).get("ParameterStructure", {}).get("Id")
                if root_id is None:
                    raise AutomationError("INVALID_STRUCTURE", "Missing parameter root group ID")
                wire_fields["GroupId"] = root_id
            wire_fields["InsertIndex"] = 0
        if op.kind == "create_parameter" and wire_fields.get("GroupId") == "":
            wire_fields.pop("GroupId")
        requests.append({"method": METHODS[op.kind], "data": wire_fields})
        differences.append({"kind": op.kind, "id": op.id, "before": old, "after": table.get(op.id)})
    return {
        "requests": requests,
        "differences": differences,
        "desired": desired,
        "object_checks": object_checks,
        "outcome": "change" if requests else "no_change",
    }


def check_desired(before: dict, after: dict, plan: dict):
    desired = plan["desired"]
    actual = after["normalized"]
    # Preserve every existing field. For a new entry the Editor may supply additional
    # label metadata; permit those additions but never ignore an existing attribute.
    for bucket in desired:
        if set(actual[bucket]) != set(desired[bucket]):
            raise AutomationError(
                "POSTCONDITION_FAILED", "Unexpected ID additions or removals", bucket=bucket
            )
        for object_id, wanted in desired[bucket].items():
            got = actual[bucket][object_id]
            if any(got.get(k) != v for k, v in wanted.items()):
                raise AutomationError(
                    "POSTCONDITION_FAILED",
                    "Readback differs from planned structure",
                    bucket=bucket,
                    id=object_id,
                    expected=wanted,
                    actual=got,
                )
            original = before["normalized"][bucket].get(object_id)
            if original and set(got) != set(original):
                raise AutomationError(
                    "POSTCONDITION_FAILED", "Unexpected metadata change", id=object_id
                )
