# coding=utf-8
"""Methods to write URBANopt system parameters to OpenStudio."""
from __future__ import division
from honeybee_openstudio.openstudio import OSModel

from .des import ghe_des_to_openstudio, gen5_des_to_openstudio, gen4_des_to_openstudio
from .ets import heat_pump_ets_to_openstudio, heat_exchanger_ets_to_openstudio


def sys_dict_to_openstudio(sys_dict, seed_model=None, geojson_dict=None):
    """Create an OpenStudio Model from a dictionary of an URBANopt system parameter.

    Assuming that the file paths within the system parameter all point to valid
    files, the resulting OpenStudio Model will include all equipment of the
    DES loop (ground heat exchangers, heating/cooling plants). It will also
    include hot/chilled water loops for all buildings in the DES with the
    building loads represented using LoadPRofile:Plant objects.

    No building air loops, zone equipment, or geometry will be in the resulting
    model, making it a plant-only model that can be simulated quickly.

    Args:
        sys_dict: The URBANopt system parameter dictionary to be converted
            into an OpenStudio Model.
        seed_model: An optional OpenStudio Model object to which the district
            energy system will be added. If None, a new OpenStudio Model will be
            initialized within this method. (Default: None).
        geojson_dict: An optional URBANopt feature GeoJSON dictionary, which can
            be used to further customize the OpenStudio model. When supplied,
            the lengths of ThermalConnectors in the loop will be used to
            account for pipe heat losses in the resulting OpenStudio model and
            any customizations of heat rejection types and supplemental heating
            types will be accounted for in the model.
    """
    # create the OpenStudio model object and set properties for speed
    os_model = OSModel() if seed_model is None else seed_model

    # translate the district thermal loop based on the type of system
    des_dict = sys_dict['district_system']
    if 'fifth_generation' in des_dict:
        if 'ghe_parameters' in des_dict['fifth_generation']:
            hp_loop = ghe_des_to_openstudio(des_dict, os_model, geojson_dict)
        else:  # a regular old gen5 district system
            hp_loop = gen5_des_to_openstudio(des_dict, os_model, geojson_dict)
    elif 'fourth_generation' in des_dict:
        chw_loop, hw_loop = gen4_des_to_openstudio(des_dict, os_model, geojson_dict)
    else:  # currently unrecognized district system
        for key in des_dict:
            msg = 'District system type "{}" is not recognized.'.format(key)
            raise ValueError(msg)

    # translate the building ETS
    for bldg_dict in sys_dict['buildings']:
        if 'fifth_gen_ets_parameters' in bldg_dict:
            heat_pump_ets_to_openstudio(bldg_dict, hp_loop, os_model)
        elif 'ets_indirect_parameters' in bldg_dict:
            heat_exchanger_ets_to_openstudio(bldg_dict, os_model)

    return os_model


def sys_dict_to_osm(sys_dict, seed_model=None, geojson_dict=None):
    """Translate a dictionary of an URBANopt system parameter to an OSM string.

    Args:
        sys_dict: The URBANopt system parameter dictionary to be converted
            into an OpenStudio Model.
        seed_model: An optional OpenStudio Model object to which the district
            energy system will be added. If None, a new OpenStudio Model will be
            initialized within this method. (Default: None).
        geojson_dict: An optional URBANopt feature GeoJSON dictionary, which can
            be used to further customize the OpenStudio model. When supplied,
            the lengths of ThermalConnectors in the loop will be used to
            account for pipe heat losses in the resulting OpenStudio model and
            any customizations of heat rejection types and supplemental heating
            types will be accounted for in the model.
    """
    # translate the Honeybee Model to an OpenStudio Model
    os_model = sys_dict_to_openstudio(sys_dict, seed_model, geojson_dict)
    return str(os_model)
