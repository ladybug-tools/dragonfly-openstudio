# coding=utf-8
import json

from honeybee_openstudio.openstudio import OSModel
from dragonfly_openstudio.des import ghe_des_to_openstudio


def test_ghe_des_to_openstudio():
    """Test the ghe_des_to_openstudio function."""
    sp_json = './tests/assets/small_ghe/system_params.json'
    with open(sp_json) as json_file:
        sys_dict = json.load(json_file)
    des_dict = sys_dict['district_system']

    os_model = OSModel()
    des_loop = ghe_des_to_openstudio(des_dict, os_model)
    assert des_loop.nameString() == 'Fifth Gen Ground HX Loop'
    des_loop_str = str(des_loop)
    assert des_loop_str.startswith('OS:PlantLoop,')
    print(str(des_loop))
