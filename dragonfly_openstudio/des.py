# coding=utf-8
"""Methods to write Dragonfly District Energy Systems (DES) to OpenStudio."""
from __future__ import division
import os

from honeybee_openstudio.openstudio import openstudio_model
from honeybee_openstudio.hvac.standards.schedule import create_constant_schedule_ruleset
from honeybee_openstudio.hvac.standards.hvac_systems import model_add_waterside_economizer, \
    model_add_vsd_twr_fan_curve
from honeybee_openstudio.hvac.standards.cooling_tower import \
    prototype_apply_condenser_water_temperatures
from honeybee_openstudio.hvac.standards.central_air_source_heat_pump import \
    create_central_air_source_heat_pump
from dragonfly_energy.des.ghe import GroundHeatExchanger


def ghe_des_to_openstudio(des_dict, os_model, geojson_dict=None):
    """Convert a dictionary of a district_system with ghe_parameters to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
        geojson_dict: An optional URBANopt feature GeoJSON dictionary, which can
            be used to further customize the OpenStudio model.
    """
    # get the various sub-objects of the main dictionary
    des_dict = des_dict['fifth_generation']
    central_pump = des_dict['central_pump_parameters']
    soil = des_dict['soil']
    horiz_pipe = des_dict['horizontal_piping_parameters']
    fluid = des_dict['ghe_parameters']['fluid']
    grout = des_dict['ghe_parameters']['grout']
    pipe = des_dict['ghe_parameters']['pipe']
    design = des_dict['ghe_parameters']['design']
    borehole = des_dict['ghe_parameters']['borehole']
    bore_fields = des_dict['ghe_parameters']['borefields']

    # create ground hx loop
    ground_hx_loop = openstudio_model.PlantLoop(os_model)
    ground_hx_loop.setName('Fifth Gen Ground HX Loop')
    loop_name = ground_hx_loop.nameString()

    # ground hx loop sizing and controls
    ground_hx_loop.setMinimumLoopTemperature(design['min_eft'] - 1)
    ground_hx_loop.setMaximumLoopTemperature(design['max_eft'] + 1)
    sizing_plant = ground_hx_loop.sizingPlant()
    sizing_plant.setLoopType('Condenser')
    sizing_plant.setDesignLoopExitTemperature(design['max_eft'])
    sizing_plant.setLoopDesignTemperatureDifference(11.0)
    sizing_plant.setSizingOption('Coincident')
    hp_high_t_sch = openstudio_model.ScheduleConstant(os_model)
    hp_high_t_sch.setName('{} High Temp - {}C'.format(loop_name, int(design['max_eft'])))
    hp_high_t_sch.setValue(design['max_eft'])
    hp_low_t_sch = openstudio_model.ScheduleConstant(os_model)
    hp_low_t_sch.setName('{} Low Temp - {}C'.format(loop_name, int(design['min_eft'])))
    hp_low_t_sch.setValue(design['min_eft'])

    # create the central pump
    pump = openstudio_model.PumpVariableSpeed(os_model)
    pump.setName('{} Pump'.format(loop_name))
    pump.setRatedPumpHead(central_pump['pump_design_head'])
    if not central_pump['pump_flow_rate_autosized']:
        pump.setRatedFlowRate(central_pump['pump_flow_rate'])
    pump.setPumpControlType('Intermittent')
    pump.addToNode(ground_hx_loop.supplyInletNode())

    # schedule to establish a target temperature for the loop
    loop_target_temp = 24.0  # target temperature
    hx_temp_sch = openstudio_model.ScheduleConstant(os_model)
    hx_temp_sch.setName('Ground HX Target Temp - {}C'.format(loop_target_temp))
    hx_temp_sch.setValue(loop_target_temp)
    loop_stpt_manager = openstudio_model.SetpointManagerScheduled(os_model, hx_temp_sch)
    loop_stpt_manager.setName('{} Supply Outlet Setpoint'.format(loop_name))
    loop_stpt_manager.addToNode(ground_hx_loop.supplyOutletNode())

    # add heat rejection equipment to prevent the loop from overheating during peak
    heat_rejection_type = 'CoolingTower'
    if geojson_dict and 'project' in geojson_dict and \
            'heat_rejection_type' in geojson_dict['project']:
        heat_rejection_type = geojson_dict['project']['heat_rejection_type']
    cooling_stpt = openstudio_model.SetpointManagerScheduled(os_model, hp_high_t_sch)
    hr_equip = gen5_heat_rejection(ground_hx_loop, cooling_stpt, os_model, heat_rejection_type)
    if 'FluidCooler' in heat_rejection_type:  # ensure that loop can be cooled
        hr_equip.setDesignEnteringAirTemperature(design['max_eft'] - 3)

    # add supplemental heating to prevent the loop from becoming too cold
    supplemental_heat_type = 'Electricity'
    if geojson_dict and 'project' in geojson_dict and \
            'supplemental_heat_type' in geojson_dict['project']:
        supplemental_heat_type = geojson_dict['project']['supplemental_heat_type']
    heating_stpt = openstudio_model.SetpointManagerScheduled(os_model, hp_low_t_sch)
    gen5_supplemental_heat(ground_hx_loop, heating_stpt, os_model, supplemental_heat_type)

    # add ground loop pipes
    _gen5_horizontal_pipes(
        horiz_pipe, soil, central_pump, ground_hx_loop, os_model, geojson_dict
    )

    # add the ground heat exchangers
    ghe_dir = des_dict['ghe_parameters']['ghe_dir']
    assert os.path.isdir(ghe_dir), \
        'No GHE sizing results were found at" {}.'.format(ghe_dir)
    for ghe_id in os.listdir(ghe_dir):
        # find the matching GHE in the loop
        for ghe in bore_fields:
            if ghe_id == ghe['ghe_id']:
                matched_ghe = ghe
                break
        else:
            msg = 'No GHE in the connected ghe_dir matches with the GHE ' \
                '"{}" in the system parameters.'.format(ghe_id)
            raise ValueError(msg)

        # create the OpenStudio GroundHeatExchangerVertical and set all properties
        ground_hx = openstudio_model.GroundHeatExchangerVertical(os_model)
        if 'autosized_birectangle_constrained_borefield' in matched_ghe:
            ghe_dict = matched_ghe['autosized_birectangle_constrained_borefield']
            borehole_count = ghe_dict['number_of_boreholes']
        elif 'pre_designed_borefield' in matched_ghe:
            ghe_dict = matched_ghe['pre_designed_borefield']
            borehole_count = len(ghe_dict['borehole_x_coordinates'])
        else:
            continue  # not a GHE type that can be translated yet
        design_flow = design['flow_rate'] * borehole_count \
            if design['flow_type'] == 'borehole' else design['flow_rate']
        design_flow = design_flow / 1000  # convert L/s to m3/s
        ground_hx.setDesignFlowRate(design_flow)
        ground_hx.setNumberofBoreHoles(borehole_count)
        ground_hx.setBoreHoleTopDepth(borehole['buried_depth'])
        ground_hx.setBoreHoleLength(ghe_dict['borehole_length'])
        ground_hx.setBoreHoleRadius(borehole['diameter'] / 2)
        ground_hx.setGroundTemperature(soil['undisturbed_temp'])
        ground_hx.setGroundThermalConductivity(soil['conductivity'])
        ground_hx.setGroundThermalHeatCapacity(soil['rho_cp'])
        ground_hx.setGroutThermalConductivity(grout['conductivity'])
        ground_hx.setPipeThermalConductivity(pipe['conductivity'])
        ground_hx.setPipeOutDiameter(pipe['outer_diameter'])
        ground_hx.setPipeThickness((pipe['outer_diameter'] - pipe['inner_diameter']) / 2)
        ground_hx.setUTubeDistance(pipe['shank_spacing'])

        # load the G function and assign it
        g_func_file = os.path.join(ghe_dir, ghe_id, 'Gfunction.csv')
        g_func = GroundHeatExchanger.load_g_function(g_func_file)
        for g_func_ln, g_func_value in g_func:
            ground_hx.addGFunction(g_func_ln, g_func_value)

        # add the GHE to the plant loop
        ground_hx_loop.addSupplyBranchForComponent(ground_hx)

    # set the loop fluid if it is not 100% water
    # this must be set last because adding new equipment causes the loop to reset
    if fluid['fluid_name'] != 'Water' and fluid['concentration_percent'] != 0:
        ground_hx_loop.setGlycolConcentration(int(fluid['concentration_percent'] * 100))
        if fluid['fluid_name'] in ('EthyleneGlycol', 'EthylAlcohol'):
            ground_hx_loop.setFluidType('EthyleneGlycol')
        else:
            ground_hx_loop.setFluidType('PropyleneGlycol')

    return ground_hx_loop


def gen5_des_to_openstudio(des_dict, os_model, geojson_dict=None):
    """Convert a dictionary of a fifth_generation district_system to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
        geojson_dict: An optional URBANopt feature GeoJSON dictionary, which can
            be used to further customize the OpenStudio model.
    """
    # get the various sub-objects of the main dictionary
    des_dict = des_dict['fifth_generation']
    central_pump = des_dict['central_pump_parameters']
    soil = des_dict['soil']
    horiz_pipe = des_dict['horizontal_piping_parameters']

    # create heat pump loop
    heat_pump_water_loop = openstudio_model.PlantLoop(os_model)
    heat_pump_water_loop.setLoadDistributionScheme('SequentialLoad')
    heat_pump_water_loop.setName('Fifth Gen Heat Pump Loop')

    # hot water loop sizing and controls
    sup_wtr_high_temp_c = 30.0
    sup_wtr_low_temp_c = 5.0
    dsgn_sup_wtr_temp_delt_k = 11.0

    sizing_plant = heat_pump_water_loop.sizingPlant()
    sizing_plant.setLoopType('Condenser')
    heat_pump_water_loop.setMinimumLoopTemperature(3.0)
    heat_pump_water_loop.setMaximumLoopTemperature(35.0)
    sizing_plant.setDesignLoopExitTemperature(sup_wtr_high_temp_c)
    sizing_plant.setLoopDesignTemperatureDifference(dsgn_sup_wtr_temp_delt_k)
    loop_name = heat_pump_water_loop.nameString()
    hp_high_temp_sch = create_constant_schedule_ruleset(
        os_model, sup_wtr_high_temp_c,
        name='{} High Temp - {}C'.format(loop_name, int(sup_wtr_high_temp_c)),
        schedule_type_limit='Temperature')
    hp_low_temp_sch = create_constant_schedule_ruleset(
        os_model, sup_wtr_low_temp_c,
        name='{} Low Temp - {}C'.format(loop_name, int(sup_wtr_low_temp_c)),
        schedule_type_limit='Temperature')
    hp_stpt_manager = openstudio_model.SetpointManagerScheduledDualSetpoint(os_model)
    hp_stpt_manager.setName('{} Scheduled Dual Setpoint'.format(loop_name))
    hp_stpt_manager.setHighSetpointSchedule(hp_high_temp_sch)
    hp_stpt_manager.setLowSetpointSchedule(hp_low_temp_sch)
    hp_stpt_manager.addToNode(heat_pump_water_loop.supplyOutletNode())

    # create pump
    hp_pump = openstudio_model.PumpVariableSpeed(os_model)
    hp_pump.setName('{} Pump'.format(loop_name))
    if not central_pump['pump_flow_rate_autosized']:
        hp_pump.setRatedFlowRate(central_pump['pump_flow_rate'])
    else:
        hp_pump.setRatedPumpHead(179300)
    hp_pump.setPumpControlType('Intermittent')
    hp_pump.addToNode(heat_pump_water_loop.supplyInletNode())

    # create heat rejection equipment and add to the loop
    heat_rejection_type = 'CoolingTower'
    if geojson_dict and 'project' in geojson_dict and \
            'heat_rejection_type' in geojson_dict['project']:
        heat_rejection_type = geojson_dict['project']['heat_rejection_type']
    cooling_stpt = openstudio_model.SetpointManagerScheduledDualSetpoint(os_model)
    cooling_stpt.setHighSetpointSchedule(hp_high_temp_sch)
    cooling_stpt.setLowSetpointSchedule(hp_low_temp_sch)
    hr_equip = gen5_heat_rejection(heat_pump_water_loop, cooling_stpt, os_model, heat_rejection_type)
    if 'FluidCooler' in heat_rejection_type:  # ensure that loop can be cooled
        hr_equip.setDesignEnteringAirTemperature(33.0)
        sizing_plant.setDesignLoopExitTemperature(35.0)

    # add supplemental heating to prevent the loop from becoming too cold
    supplemental_heat_type = 'Electricity'
    if geojson_dict and 'project' in geojson_dict and \
            'supplemental_heat_type' in geojson_dict['project']:
        supplemental_heat_type = geojson_dict['project']['supplemental_heat_type']
    heating_stpt = openstudio_model.SetpointManagerScheduledDualSetpoint(os_model)
    heating_stpt.setHighSetpointSchedule(hp_high_temp_sch)
    heating_stpt.setLowSetpointSchedule(hp_low_temp_sch)
    gen5_supplemental_heat(heat_pump_water_loop, heating_stpt, os_model, supplemental_heat_type)

    # add heat pump water loop pipes
    supply_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_bypass_pipe.setName('{} Supply Bypass'.format(loop_name))
    heat_pump_water_loop.addSupplyBranchForComponent(supply_bypass_pipe)

    demand_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_bypass_pipe.setName('{} Demand Bypass'.format(loop_name))
    heat_pump_water_loop.addDemandBranchForComponent(demand_bypass_pipe)

    # add ground loop pipes
    _gen5_horizontal_pipes(
        horiz_pipe, soil, central_pump, heat_pump_water_loop, os_model, geojson_dict
    )

    return heat_pump_water_loop


def gen5_heat_rejection(heat_pump_loop, setpoint_manager, os_model,
                        heat_rejection_type='CoolingTower'):
    """Get heat rejection equipment for a fifth generation thermal loop.

    Args:
        heat_pump_loop: The heat pump loop to which the heat rejection equipment
            is to be added.
        setpoint_manager: The setpoint manager for the heat rejection equipment.
        os_model: The OpenStudio Model to which the equipment is to be added.
        heat_rejection_type: Text for the equipment used to cool a fifth generation
            loop when it overheats. Note that choosing None will usually cause a
            simulation failure unless there is a very large ground heat exchanger
            on the loop. Choose from the options below. (Default: CoolingTower).

            * CoolingTower
            * CoolingTowerSingleSpeed
            * CoolingTowerTwoSpeed
            * CoolingTowerVariableSpeed
            * FluidCooler
            * FluidCoolerSingleSpeed
            * FluidCoolerTwoSpeed
            * EvaporativeFluidCooler
            * EvaporativeFluidCoolerSingleSpeed
            * EvaporativeFluidCoolerTwoSpeed
            * DistrictCooling
            * None
    """
    # set up variables used for multiple equipment types
    loop_name = heat_pump_loop.nameString()
    fc_size_type = 'UFactorTimesAreaAndDesignWaterFlowRate'

    # create the equipment based on the heat_rejection_type
    if heat_rejection_type == 'None':
        return  # let's hope that there is a large enough GHE
    elif heat_rejection_type == 'DistrictCooling':
        cooling_equipment = openstudio_model.DistrictCooling(os_model)
        cooling_equipment.setName('{} District Cooling'.format(loop_name))
        cooling_equipment.autosizeNominalCapacity()
        setpoint_manager.setName('{} District Cooling Setpoint'.format(loop_name))
    else:
        if heat_rejection_type in ('CoolingTower', 'CoolingTowerVariableSpeed'):
            cooling_equipment = openstudio_model.CoolingTowerVariableSpeed(os_model)
            cooling_equipment.setName('{} Cooling Tower'.format(loop_name))
            setpoint_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
        elif heat_rejection_type == 'CoolingTowerSingleSpeed':
            cooling_equipment = openstudio_model.CoolingTowerSingleSpeed(os_model)
            cooling_equipment.setName('{} Cooling Tower'.format(loop_name))
            setpoint_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
        elif heat_rejection_type == 'CoolingTowerTwoSpeed':
            cooling_equipment = openstudio_model.CoolingTowerTwoSpeed(os_model)
            cooling_equipment.setName('{} Cooling Tower'.format(loop_name))
            setpoint_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
        elif heat_rejection_type in ('FluidCooler', 'FluidCoolerTwoSpeed'):
            cooling_equipment = openstudio_model.FluidCoolerTwoSpeed(os_model)
            cooling_equipment.setName('{} Fluid Cooler'.format(loop_name))
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            cooling_equipment.autosizeDesignWaterFlowRate()
            cooling_equipment.autosizeHighFanSpeedAirFlowRate()
            cooling_equipment.autosizeLowFanSpeedAirFlowRate()
        elif heat_rejection_type == 'FluidCoolerSingleSpeed':
            cooling_equipment = openstudio_model.FluidCoolerSingleSpeed(os_model)
            cooling_equipment.setName('{} Fluid Cooler'.format(loop_name))
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            cooling_equipment.autosizeDesignWaterFlowRate()
            cooling_equipment.autosizeDesignAirFlowRate()
        elif heat_rejection_type in ('EvaporativeFluidCooler', 'EvaporativeFluidCoolerTwoSpeed'):
            cooling_equipment = openstudio_model.EvaporativeFluidCoolerTwoSpeed(os_model)
            cooling_equipment.setName('{} Evaporative Fluid Cooler'.format(loop_name))
            cooling_equipment.setDesignSprayWaterFlowRate(0.002208)
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
        elif heat_rejection_type == 'EvaporativeFluidCoolerSingleSpeed':
            cooling_equipment = openstudio_model.EvaporativeFluidCoolerSingleSpeed(os_model)
            cooling_equipment.setName('{} Evaporative Fluid Cooler'.format(loop_name))
            cooling_equipment.setDesignSprayWaterFlowRate(0.002208)
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
        else:
            msg = 'Heat rejection type "{}" is not recognized.'.format(heat_rejection_type)
            raise ValueError(msg)
    heat_pump_loop.addSupplyBranchForComponent(cooling_equipment)
    equip_out_node = cooling_equipment.outletModelObject().get().to_Node().get()
    setpoint_manager.addToNode(equip_out_node)
    return cooling_equipment


def gen5_supplemental_heat(heat_pump_loop, setpoint_manager, os_model,
                           supplemental_heat_type='Electricity'):
    """Get supplemental heating equipment for a fifth generation thermal loop.

    Args:
        heat_pump_loop: The heat pump loop to which the heating equipment
            is to be added.
        setpoint_manager: The setpoint manager for the heating equipment.
        os_model: The OpenStudio Model to which the equipment is to be added.
        supplemental_heat_type: Text for the equipment used to heat the loop
            when it requires supplemental heating. Note that choosing None will
            usually cause a simulation failure unless there is a very large
            ground heat exchanger on the loop. Choose from the options below.
            Choose from the options below. (Default: Electricity).

            * Electricity
            * NaturalGas
            * DistrictHeating
            * None
    """
    # set up variables used for multiple equipment types
    loop_name = heat_pump_loop.nameString()

    # create heating equipment and add to the loop
    if supplemental_heat_type == 'None':
        return  # let's hope that there is a large enough GHE
    elif supplemental_heat_type == 'DistrictHeating':
        heating_equipment = openstudio_model.DistrictHeating(os_model)
        heating_equipment.setName('{} Supplemental District Heating'.format(loop_name))
        heating_equipment.autosizeNominalCapacity()
        heat_pump_loop.addSupplyBranchForComponent(heating_equipment)
        setpoint_manager.setName('{} Supplemental District Heating Setpoint'.format(loop_name))
    elif supplemental_heat_type in ('Electricity', 'NaturalGas'):
        heating_equipment = openstudio_model.BoilerHotWater(os_model)
        heating_equipment.setName('{} Supplemental Boiler'.format(heat_pump_loop.nameString()))
        if supplemental_heat_type == 'Electricity':
            heating_equipment.setNominalThermalEfficiency(1.0)
            heating_equipment.setFuelType('Electricity')
        else:
            heating_equipment.setNominalThermalEfficiency(0.9)
            heating_equipment.setFuelType('NaturalGas')
        heat_pump_loop.addSupplyBranchForComponent(heating_equipment)
        setpoint_manager.setName('{} Supplemental Boiler Setpoint'.format(loop_name))
    else:
        msg = 'Supplemental heating type "{}" is not valid'.format(supplemental_heat_type)
        raise ValueError(msg)
    equip_out_node = heating_equipment.outletModelObject().get().to_Node().get()
    setpoint_manager.addToNode(equip_out_node)
    return heating_equipment


def _gen5_horizontal_pipes(horiz_pipe, soil, central_pump, heat_pump_loop, os_model,
                           geojson_dict=None):
    """Create pipes to account for losses in a fifth generation thermal loop."""
    if True:  # TODO: change back to if geojson_dict is None
        supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
        demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    else:  # TODO: Add support for buried pipes using the info in the GeoJSON dict
        # deserialize the ThermalConnectors to get their lengths
        total_length = 0
        for feature in geojson_dict['features']:
            if feature['properties']['type'] == 'ThermalConnector':
                total_length += feature['properties']['total_length']

        # get the diameter of the pipe
        if 'hydraulic_diameter' not in horiz_pipe:
            # try to autosize based on total length
            try:
                from thermalnetwork.pipe import Pipe
                network_pipe = Pipe(
                    dimension_ratio=horiz_pipe['diameter_ratio'],
                    length=total_length
                )
                pressure_loss = horiz_pipe['pressure_drop_per_meter']
                design_vol_flow = central_pump['pump_flow_rate']  \
                    if 'pump_flow_rate' in central_pump else 0.05  # use hard-coded 50L/s
                pipe_diameter = network_pipe.size_hydraulic_diameter(
                    design_vol_flow, pressure_loss)
            except ImportError:  # no package installed; just use something reasonable
                pipe_diameter = 0.15
        else:
            pipe_diameter = horiz_pipe['hydraulic_diameter']

        # create the pipe material
        pipe_thickness = pipe_diameter / horiz_pipe['diameter_ratio']
        pipe_mat = openstudio_model.StandardOpaqueMaterial(os_model)
        pipe_mat.setName('Horizontal Pipe HDPE')
        pipe_mat.setThickness(pipe_thickness)
        pipe_mat.setConductivity(0.5)
        pipe_mat.setDensity(950.0)
        pipe_mat.setSpecificHeat(2000.0)
        pipe_mat.setRoughness('VerySmooth')
        # create the insulation material
        insulation = openstudio_model.StandardOpaqueMaterial(os_model)
        insulation.setName('Horizontal Pipe Insulation')
        insulation.setThickness(horiz_pipe['insulation_thickness'])
        insulation.setConductivity(horiz_pipe['insulation_conductivity'])
        insulation.setDensity(43.0)
        insulation.setSpecificHeat(1210.0)
        insulation.setRoughness('MediumRough')
        # bring everything together into a pipe construction
        pipe_con = openstudio_model.Construction(os_model)
        pipe_con.setName('Horizontal Pipe Construction')
        os_materials = openstudio_model.MaterialVector()
        for os_material in (insulation, pipe_mat):
            try:
                os_materials.append(os_material)
            except AttributeError:  # using OpenStudio .NET bindings
                os_materials.Add(os_material)
        pipe_con.setLayers(os_materials)

        # apply properties to the pipes
        supply_outlet_pipe = openstudio_model.PipeOutdoor(os_model)
        demand_outlet_pipe = openstudio_model.PipeOutdoor(os_model)
        for os_pipe in (supply_outlet_pipe, demand_outlet_pipe):
            os_pipe.setConstruction(pipe_con)
            os_pipe.setPipeInsideDiameter(pipe_diameter)
            os_pipe.setPipeLength(total_length / 2)

    # add all of the pipes to the model
    loop_name = heat_pump_loop.nameString()
    demand_inlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_outlet_pipe.setName('{} Supply Outlet'.format(loop_name))
    supply_outlet_pipe.addToNode(heat_pump_loop.supplyOutletNode())
    demand_inlet_pipe.setName('{} Demand Inlet'.format(loop_name))
    demand_inlet_pipe.addToNode(heat_pump_loop.demandInletNode())
    demand_outlet_pipe.setName('{} Demand Outlet'.format(loop_name))
    demand_outlet_pipe.addToNode(heat_pump_loop.demandOutletNode())


def gen4_des_to_openstudio(des_dict, os_model, geojson_dict=None):
    """Convert a dictionary of a fourth_generation district_system to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
        geojson_dict: An optional URBANopt feature GeoJSON dictionary, which can
            be used to further customize the OpenStudio model.
    """
    # get the various sub-objects of the main dictionary
    des_dict = des_dict['fourth_generation']
    cooling_par = des_dict['central_cooling_plant_parameters']
    heating_par = des_dict['central_heating_plant_parameters']
    if 'hhw_pump_head' not in heating_par:  # use chw pump head until it is exposed
        heating_par['hhw_pump_head'] = cooling_par['chw_pump_head']

    # create the condenser outdoor loop
    cw_loop = gen4_condenser_loop(cooling_par, os_model)
    # create the chilled water loop
    chw_loop = gen4_chilled_water_loop(cooling_par, geojson_dict, cw_loop, os_model)
    # create the hot water loop
    hw_loop = gen4_hot_water_loop(heating_par, geojson_dict, os_model)

    return chw_loop, hw_loop


def gen4_condenser_loop(cooling_par, os_model):
    """Get a condenser loop for a fourth generation district system.

    Args:
        cooling_par: The central_cooling_plant_parameters from the fourth generation
            system parameter dictionary.
        os_model: The OpenStudio Model to which the equipment is to be added.
    """
    # extract the system parameters relevant to the cooling tower
    cw_temp = cooling_par['temp_cw_in_nominal']
    ct_dt = cooling_par['cooling_tower_water_temperature_difference_nominal']
    wb_temp = cooling_par['temp_air_wb_nominal']
    approach_dt = cooling_par['delta_temp_approach']
    pump_head = cooling_par['cw_pump_head']

    # create the condenser outdoor loop
    cw_loop = openstudio_model.PlantLoop(os_model)
    cw_loop.setName('Central Condenser Water Loop')
    cw_name = cw_loop.nameString()
    cw_loop.setMinimumLoopTemperature(5.0)
    cw_loop.setMaximumLoopTemperature(80.0)
    sizing_plant = cw_loop.sizingPlant()
    sizing_plant.setLoopType('Condenser')
    sizing_plant.setDesignLoopExitTemperature(cw_temp)
    sizing_plant.setLoopDesignTemperatureDifference(ct_dt)
    sizing_plant.setSizingOption('Coincident')
    sizing_plant.setZoneTimestepsinAveragingWindow(6)
    sizing_plant.setCoincidentSizingFactorMode('GlobalCoolingSizingFactor')

    # follow outdoor air wetbulb with given approach temperature
    cw_stpt_manager = openstudio_model.SetpointManagerFollowOutdoorAirTemperature(os_model)
    s_pt_name = '{} Setpoint Manager Follow OATwb with {}C Approach'.format(cw_name, approach_dt)
    cw_stpt_manager.setName(s_pt_name)
    cw_stpt_manager.setReferenceTemperatureType('OutdoorAirWetBulb')
    cw_stpt_manager.setMaximumSetpointTemperature(cw_temp)
    cw_stpt_manager.setMinimumSetpointTemperature(cw_temp - 8)
    cw_stpt_manager.setOffsetTemperatureDifference(approach_dt)
    cw_stpt_manager.addToNode(cw_loop.supplyOutletNode())

    # add a condenser water pump
    cw_pump = openstudio_model.PumpVariableSpeed(os_model)
    cw_pump.setName('{} Variable Pump'.format(cw_name))
    cw_pump.setPumpControlType('Intermittent')
    cw_pump.setMotorEfficiency(0.9)
    cw_pump.setRatedPumpHead(pump_head)
    cw_pump.addToNode(cw_loop.supplyInletNode())

    # add a cooling tower
    cooling_tower = openstudio_model.CoolingTowerVariableSpeed(os_model)
    cooling_tower.setDesignRangeTemperature(ct_dt)
    cooling_tower.setDesignApproachTemperature(approach_dt)
    cooling_tower.setFractionofTowerCapacityinFreeConvectionRegime(0.125)
    twr_fan_curve = model_add_vsd_twr_fan_curve(os_model)
    cooling_tower.setFanPowerRatioFunctionofAirFlowRateRatioCurve(twr_fan_curve)
    twr_name = 'Propeller Variable Speed Fan Open Cooling Tower'
    cooling_tower.setName(twr_name)
    cw_loop.addSupplyBranchForComponent(cooling_tower)
    prototype_apply_condenser_water_temperatures(cw_loop, design_wet_bulb_c=wb_temp)

    # Condenser water loop pipes
    cooling_tower_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    pipe_name = '{} Cooling Tower Bypass'.format(cw_name)
    cooling_tower_bypass_pipe.setName(pipe_name)
    cw_loop.addSupplyBranchForComponent(cooling_tower_bypass_pipe)

    chiller_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    pipe_name = '{} Chiller Bypass'.format(cw_name)
    chiller_bypass_pipe.setName(pipe_name)
    cw_loop.addDemandBranchForComponent(chiller_bypass_pipe)

    supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_outlet_pipe.setName('{} Supply Outlet'.format(cw_name))
    supply_outlet_pipe.addToNode(cw_loop.supplyOutletNode())

    demand_inlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_inlet_pipe.setName('{} Demand Inlet'.format(cw_name))
    demand_inlet_pipe.addToNode(cw_loop.demandInletNode())

    demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_outlet_pipe.setName('{} Demand Outlet'.format(cw_name))
    demand_outlet_pipe.addToNode(cw_loop.demandOutletNode())

    return cw_loop


def gen4_chilled_water_loop(cooling_par, geojson_dict, cw_loop, os_model):
    """Get a chilled water loop for a fourth generation district system.

    Args:
        cooling_par: The central_cooling_plant_parameters from the fourth generation
            system parameter dictionary.
        geojson_dict: None or the URBANopt feature GeoJSON dictionary, which can
            be used to further customize the loop.
        cw_loop: The condenser water loop to which the chilled water will be connected.
        os_model: The OpenStudio Model to which the equipment is to be added.
    """
    # extract the system parameters relevant to the chilled water loop
    chw_temp = cooling_par['temp_setpoint_chw']
    pump_head = cooling_par['chw_pump_head']

    # create a chilled water loop
    chw_loop = openstudio_model.PlantLoop(os_model)
    chw_loop.setName('Central Chilled Water Loop')
    chw_name = chw_loop.nameString()
    chw_loop.setMaximumLoopTemperature(40.0)
    chw_sizing_plant = chw_loop.sizingPlant()
    chw_sizing_plant.setDesignLoopExitTemperature(chw_temp)
    chw_sizing_plant.setLoopDesignTemperatureDifference(4.0)
    chw_sizing_plant.setLoopType('Cooling')
    chw_temp_sch = create_constant_schedule_ruleset(
        os_model, chw_temp, schedule_type_limit='Temperature',
        name='{} Temp - {}C'.format(chw_name, int(chw_temp)))
    chw_stpt_manager = openstudio_model.SetpointManagerScheduled(os_model, chw_temp_sch)
    chw_stpt_manager.setName('{} Setpoint Manager'.format(chw_name))
    chw_stpt_manager.addToNode(chw_loop.supplyOutletNode())

    # add a pump for the chilled water loop
    chw_pump = openstudio_model.PumpVariableSpeed(os_model)
    chw_pump.setName('{} Pump'.format(chw_name))
    chw_pump.setRatedPumpHead(pump_head)
    chw_pump.setMotorEfficiency(0.9)
    chw_pump.setPumpControlType('Intermittent')
    chw_pump.addToNode(chw_loop.supplyInletNode())

    # add a chiller
    chiller = openstudio_model.ChillerElectricEIR(os_model)
    ch_name = 'Central WaterCooled Centrifugal Chiller with Condenser'
    chiller.setName(ch_name)
    chw_loop.addSupplyBranchForComponent(chiller)
    chiller.setReferenceLeavingChilledWaterTemperature(chw_temp)
    chiller.setLeavingChilledWaterLowerTemperatureLimit(2.0)
    chiller.setReferenceEnteringCondenserFluidTemperature(35.0)
    chiller.setMinimumPartLoadRatio(0.15)
    chiller.setMaximumPartLoadRatio(1.0)
    chiller.setOptimumPartLoadRatio(1.0)
    chiller.setMinimumUnloadingRatio(0.25)
    chiller.setChillerFlowMode('ConstantFlow')
    chiller.setReferenceCOP(round(3.517 / 0.66, 3))

    # connect the chiller to the condenser loop
    cw_loop.addDemandBranchForComponent(chiller)
    chiller.setCondenserType('WaterCooled')

    # enable waterside economizer if requested
    economizer_type = 'None'
    if geojson_dict and 'project' in geojson_dict and \
            'economizer_type' in geojson_dict['project']:
        economizer_type = geojson_dict['project']['economizer_type']
    if economizer_type == 'Integrated':
        model_add_waterside_economizer(os_model, chw_loop, cw_loop, integrated=True)
    elif economizer_type == 'NonIntegrated':
        model_add_waterside_economizer(os_model, chw_loop, cw_loop, integrated=False)

    # chilled water loop pipes
    chiller_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    chiller_bypass_pipe.setName('{} Chiller Bypass'.format(chw_name))
    chw_loop.addSupplyBranchForComponent(chiller_bypass_pipe)

    coil_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    coil_bypass_pipe.setName('{} Coil Bypass'.format(chw_name))
    chw_loop.addDemandBranchForComponent(coil_bypass_pipe)

    supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_outlet_pipe.setName('{} Supply Outlet'.format(chw_name))
    supply_outlet_pipe.addToNode(chw_loop.supplyOutletNode())

    demand_inlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_inlet_pipe.setName('{} Demand Inlet'.format(chw_name))
    demand_inlet_pipe.addToNode(chw_loop.demandInletNode())

    demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_outlet_pipe.setName('{} Demand Outlet'.format(chw_name))
    demand_outlet_pipe.addToNode(chw_loop.demandOutletNode())

    return chw_loop


def gen4_hot_water_loop(heating_par, geojson_dict, os_model):
    """Get a hot water loop for a fourth generation district system.

    Args:
        heating_par: The central_heating_plant_parameters from the fourth generation
            system parameter dictionary.
        geojson_dict: None or the URBANopt feature GeoJSON dictionary, which can
            be used to further customize the loop.
        os_model: The OpenStudio Model to which the equipment is to be added.
    """
    # extract the system parameters relevant to the hot water loop
    hw_temp = heating_par['temp_setpoint_hhw']
    pump_head = heating_par['hhw_pump_head']

    # create the heating water loop at the specified temperature
    hw_loop = openstudio_model.PlantLoop(os_model)
    hw_loop.setName('Central Hot Water Loop')
    hw_name = hw_loop.nameString()
    hw_sizing_plant = hw_loop.sizingPlant()
    hw_sizing_plant.setDesignLoopExitTemperature(hw_temp + 11.0)
    hw_sizing_plant.setLoopDesignTemperatureDifference(11.0)
    hw_sizing_plant.setLoopType('Heating')
    hw_temp_sch = create_constant_schedule_ruleset(
        os_model, hw_temp, schedule_type_limit='Temperature',
        name='{} Temp - {}C'.format(hw_name, int(hw_temp)))
    hw_stpt_manager = openstudio_model.SetpointManagerScheduled(os_model, hw_temp_sch)
    hw_stpt_manager.setName('{} Setpoint Manager'.format(hw_name))
    hw_stpt_manager.addToNode(hw_loop.supplyOutletNode())

    # add a pump for the loop
    hw_pump = openstudio_model.PumpVariableSpeed(os_model)
    hw_pump.setName('{} Pump'.format(hw_name))
    hw_pump.setRatedPumpHead(pump_head)
    hw_pump.setMotorEfficiency(0.9)
    hw_pump.setPumpControlType('Intermittent')
    hw_pump.addToNode(hw_loop.supplyInletNode())

    # add the heating source
    heating_type = 'NaturalGas'
    if geojson_dict and 'project' in geojson_dict and \
            'heating_type' in geojson_dict['project']:
        heating_type = geojson_dict['project']['heating_type']
    if heating_type == 'DistrictHeating':
        heating_equipment = openstudio_model.DistrictHeating(os_model)
        heating_equipment.setName('{} District Heating'.format(hw_name))
        heating_equipment.autosizeNominalCapacity()
        hw_loop.addSupplyBranchForComponent(heating_equipment)
    elif heating_type in ('Electricity', 'NaturalGas'):
        heating_equipment = openstudio_model.BoilerHotWater(os_model)
        heating_equipment.setName('{} Boiler'.format(hw_name))
        if heating_type == 'Electricity':
            heating_equipment.setNominalThermalEfficiency(1.0)
            heating_equipment.setFuelType('Electricity')
        else:
            heating_equipment.setNominalThermalEfficiency(0.9)
            heating_equipment.setFuelType('NaturalGas')
        hw_loop.addSupplyBranchForComponent(heating_equipment)
    elif heating_type == 'AirSourceHeatPump':  # Central Air Source Heat Pump
        ashp_name = 'Hot_Water_Loop_Central_Air_Source_Heat_Pump'
        create_central_air_source_heat_pump(os_model, hw_loop, name=ashp_name)
    else:
        msg = 'Heating type "{}" is not valid'.format(heating_type)
        raise ValueError(msg)

    # add hot water loop pipes
    supply_equipment_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_equipment_bypass_pipe.setName('{} Supply Equipment Bypass'.format(hw_name))
    hw_loop.addSupplyBranchForComponent(supply_equipment_bypass_pipe)

    coil_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    coil_bypass_pipe.setName('{} Coil Bypass'.format(hw_name))
    hw_loop.addDemandBranchForComponent(coil_bypass_pipe)

    supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_outlet_pipe.setName('{} Supply Outlet'.format(hw_name))
    supply_outlet_pipe.addToNode(hw_loop.supplyOutletNode())

    demand_inlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_inlet_pipe.setName('{} Demand Inlet'.format(hw_name))
    demand_inlet_pipe.addToNode(hw_loop.demandInletNode())

    demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_outlet_pipe.setName('{} Demand Outlet'.format(hw_name))
    demand_outlet_pipe.addToNode(hw_loop.demandOutletNode())

    return hw_loop
