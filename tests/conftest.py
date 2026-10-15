import copy
from datetime import date

import pytest

from live2d_automation.engine import Engine
from live2d_automation.models import AutomationError, Settings


class ProtocolFixture:
    """In-memory contract fixture. Never advertised as real Cubism or native files."""

    version = "1.1.0"
    edit_cancelled = False

    def __init__(self, project):
        self.uid = "fixture-model"
        self.project = project
        self.calls = []
        self.params = {}
        self.groups = {}
        self.objects = {}
        self.allowed = self.edit_allowed = True
        self.mode = "Modeling"
        self.before = None
        self.no_effect = False
        self.fail_method = None
        self.fail_cancel = False
        self.fail_commit = False
        self.uid_after_write = None
        self.unplanned_change = False
        self.cancel_on_write = False

    async def open(self):
        pass

    async def negotiate(self):
        pass

    async def close(self):
        pass

    async def request(self, method, data):
        self.calls.append((method, copy.deepcopy(data)))
        if method == self.fail_method:
            raise AutomationError(
                "EDITOR_API_ERROR", "Injected failure", editor_error="InvalidData"
            )
        if method == "GetIsApproval":
            return {"Result": self.allowed}
        if method == "GetIsEditApproval":
            return {"Result": self.edit_allowed}
        if method == "GetCurrentEditMode":
            return {"EditMode": self.mode}
        if method == "GetCurrentModelUID":
            return {"ModelUID": self.uid}
        if method == "GetDocuments":
            return {
                "ModelingDocuments": [
                    {"DocumentFilePath": str(self.project), "Views": [{"ModelUID": self.uid}]}
                ]
            }
        if method == "GetParameterValues":
            return {
                "Parameters": [{"Id": p["Id"], "Value": p["Default"]} for p in self.params.values()]
            }
        if method == "GetParameterStructure":
            entries = []
            for group in self.groups.values():
                entries.append(
                    {
                        **group,
                        "Parameters": [
                            {k: v for k, v in p.items() if k != "GroupId"}
                            for p in self.params.values()
                            if p["GroupId"] == group["Id"]
                        ],
                    }
                )
            entries.extend(
                {k: v for k, v in p.items() if k != "GroupId"}
                for p in self.params.values()
                if not p["GroupId"]
            )
            return {"ParameterStructure": {"Name": "Root", "Id": "", "Entries": entries}}
        if method in {"GetPartStructure", "GetDeformerStructure"}:

            def children(parent):
                return [
                    {
                        "Id": o["Id"],
                        "Name": o["Name"],
                        "Type": o["Type"],
                        "Children": children(o["Id"]),
                    }
                    for o in self.objects.values()
                    if o.get("ParentId", "") == parent
                ]

            if method == "GetDeformerStructure":
                return {
                    "DeformerStructure": {
                        "Id": "",
                        "Children": [
                            {"Id": o["Id"], "Name": o["Name"], "Type": o["Type"], "Children": []}
                            for o in self.objects.values()
                            if o["Type"] != "Part"
                        ],
                    }
                }
            return {"PartStructure": {"Id": "", "Children": children("")}}
        if method == "GetObject":
            o = self.objects[data["Id"]]
            return {"Result": True, "Type": o["Type"], "Data": o}
        if method == "EditBegin":
            self.before = copy.deepcopy((self.params, self.groups, self.objects))
            return {"Result": True}
        if method == "EditEnd":
            if data["Cancel"]:
                if self.fail_cancel:
                    raise AutomationError("EDITOR_DISCONNECTED", "Injected cancel disconnect")
                self.params, self.groups, self.objects = self.before
            elif self.fail_commit:
                raise AutomationError("EDITOR_UNRESPONSIVE", "Injected commit response loss")
            return {"Result": True}
        if self.no_effect:
            return {"Result": True}
        fields = {k: v for k, v in data.items() if k != "ModelUID"}
        object_id = fields["Id"]
        if method == "AddParameterGroup":
            self.groups[object_id] = {**fields, "EntryType": "ParameterGroup"}
        elif method == "EditParameterGroup":
            self.groups[object_id].update(fields)
        elif method == "DeleteParameterGroup":
            del self.groups[object_id]
        elif method == "AddParameter":
            self.params[object_id] = {
                **fields,
                "EntryType": "Parameter",
                "GroupId": fields.get("GroupId", ""),
                "IsRepeat": False,
                "IsBlendShape": False,
                "KeyValues": [],
            }
        elif method in {"EditParameter", "MoveParameter"}:
            fields.pop("InsertIndex", None)
            self.params[object_id].update(fields)
        elif method == "DeleteParameter":
            del self.params[object_id]
        elif method in {"AddPart", "AddWarpDeformer", "AddRotationDeformer"}:
            self.objects[object_id] = {
                **fields,
                "ParentId": fields.get("ParentId", ""),
                "Type": {
                    "AddPart": "Part",
                    "AddWarpDeformer": "WarpDeformer",
                    "AddRotationDeformer": "RotationDeformer",
                }[method],
            }
        else:
            raise AssertionError(f"Unexpected fixture method: {method}")
        if self.uid_after_write:
            self.uid = self.uid_after_write
        if self.unplanned_change:
            self.groups["Unexpected"] = {
                "Id": "Unexpected",
                "Name": "unexpected",
                "EntryType": "ParameterGroup",
            }
        if self.cancel_on_write:
            self.edit_cancelled = True
        return {"Result": True}


@pytest.fixture
def engine(tmp_path):
    project = tmp_path / "protocol-fixture.cmo3"
    project.write_bytes(b"CONTRACT FIXTURE ONLY - NOT A NATIVE CUBISM MODEL")
    settings = Settings(
        state_dir=tmp_path / "state", output_dir=tmp_path / "output", port=22991, timeout=0.2
    )
    fixture = ProtocolFixture(project)
    e = Engine(settings, transport=fixture)
    e.lock_root = tmp_path / "locks"
    e.lock_root.mkdir()
    e.marker = e.lock_root / "recovery.json"
    e.listener_probe = lambda port: {
        "found": True,
        "cubism_process": True,
        "pid": -1,
        "version": "5.4.00-alpha2",
        "fixture_only": True,
    }
    e.today = lambda: date(2026, 10, 15)
    return e


@pytest.fixture
def blink_ops():
    return [
        {"kind": "create_group", "id": "Pet", "name": "Pet"},
        {
            "kind": "create_parameter",
            "id": "ParamPetTap",
            "name": "Tap",
            "group_id": "Pet",
            "min": 0,
            "default": 0,
            "max": 1,
        },
    ]
