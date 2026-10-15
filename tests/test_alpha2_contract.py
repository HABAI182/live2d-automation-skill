import copy

import pytest

from live2d_automation.models import AutomationError, Operation
from live2d_automation.structure import normalize, object_index, plan_operations


def test_observed_alpha2_names_types_and_root_list():
    got = normalize(
        {
            "Id": "ParamGroupRoot",
            "Entries": [
                {
                    "Id": "Group",
                    "GroupName": "Group name",
                    "EntryType": "ParameterGroup",
                    "Parameters": [
                        {
                            "Id": "Param",
                            "Name": "Parameter",
                            "EntryType": "Parameter",
                            "Min": 0,
                            "Default": 0,
                            "Max": 1,
                            "Repeat": False,
                            "Type": 0,
                        }
                    ],
                }
            ],
        },
        {"Id": "%Root", "Children": []},
        [{"Id": "%Root", "Children": []}],
    )
    assert got["groups"]["Group"]["Name"] == "Group name"
    assert got["parameters"]["Param"]["IsRepeat"] is False
    assert got["parameters"]["Param"]["IsBlendShape"] is False
    assert "KeyValues" not in got["parameters"]["Param"]
    with pytest.raises(AutomationError, match="Read object parameter bindings"):
        plan_operations({"normalized": got}, [Operation(kind="delete_parameter", id="Param")])


@pytest.mark.parametrize("tree", [[], [{"Id": "Unexpected"}], [{"Id": "%Root"}, {"Id": "%Root"}]])
def test_unknown_root_lists_are_rejected(tree):
    with pytest.raises(AutomationError):
        object_index(tree)


@pytest.mark.parametrize("keys", [[0, 1], [float("nan")], [2]])
async def test_missing_global_keys_are_read_from_objects_and_validated(engine, keys):
    engine.ws.params["Param"] = {
        "Id": "Param",
        "Name": "Parameter",
        "EntryType": "Parameter",
        "Min": 0,
        "Default": 0,
        "Max": 1,
        "GroupId": "",
        "KeyValues": [],
    }
    engine.ws.objects["Mesh"] = {"Id": "Mesh", "Name": "Mesh", "Type": "ArtMesh", "ParentId": ""}
    original = engine.ws.request

    async def actual_alpha2(method, data):
        if method == "GetParameterKeys":
            return {"Parameters": [{"Id": "Param", "KeyValues": keys}]}
        result = copy.deepcopy(await original(method, data))
        if method == "GetParameterStructure":
            result["ParameterStructure"]["Entries"][0].pop("KeyValues")
        return result

    engine.ws.request = actual_alpha2
    result = await engine.run("snapshot")
    if keys == [0, 1]:
        assert result.postconditions_passed
        assert result.data["snapshot"]["normalized"]["parameters"]["Param"]["KeyValues"] == keys
    else:
        assert result.status == "failed" and result.error["code"] == "INVALID_STRUCTURE"


async def test_move_to_root_uses_read_root_id_and_explicit_index(engine, blink_ops):
    initial = await engine.run("plan", expected_uid=engine.ws.uid, operations=blink_ops)
    await engine.run(
        "apply",
        expected_uid=engine.ws.uid,
        operations=blink_ops,
        idempotency_key="seed",
        dry_run=False,
        confirm=True,
        expected_snapshot_sha256=initial.data["snapshot_sha256"],
    )
    snapshot = await engine.take_snapshot(engine.ws.uid)
    snapshot["raw"]["ParameterStructure"]["Id"] = "ParamGroupRoot"
    parameter = next(iter(snapshot["normalized"]["parameters"]))
    plan = plan_operations(snapshot, [Operation(kind="move_parameter", id=parameter, group_id="")])
    assert plan["requests"][0]["data"]["GroupId"] == "ParamGroupRoot"
    assert plan["requests"][0]["data"]["InsertIndex"] == 0
    assert plan["desired"]["parameters"][parameter]["GroupId"] == ""
