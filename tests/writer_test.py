# coding=utf-8
import json
from honeybee_openstudio.openstudio import os_vector_len
from dragonfly_openstudio.writer import sys_dict_to_openstudio


def test_sys_dict_to_openstudio_ghe():
    """Test the sys_dict_to_openstudio function with a GHE DES."""
    sp_json = './tests/assets/small_ghe/system_params.json'
    with open(sp_json) as json_file:
        sys_dict = json.load(json_file)

    os_model = sys_dict_to_openstudio(sys_dict)
    loops = os_model.getPlantLoops()
    assert os_vector_len(loops) == 10
