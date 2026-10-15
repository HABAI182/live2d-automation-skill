import pytest

from live2d_automation.models import AutomationError
from live2d_automation.structure import object_index, parameter_index


@pytest.mark.parametrize(
    "tree",
    [
        {"Entries": None},
        {"Entries": [{"EntryType": "ParameterGroup", "Id": "Group", "Parameters": None}]},
    ],
)
def test_malformed_parameter_tree_fails_with_classified_error(tree):
    with pytest.raises(AutomationError) as error:
        parameter_index(tree)
    assert error.value.code == "INVALID_STRUCTURE"


def test_malformed_object_tree_fails_with_classified_error():
    with pytest.raises(AutomationError) as error:
        object_index({"Children": None})
    assert error.value.code == "INVALID_STRUCTURE"
