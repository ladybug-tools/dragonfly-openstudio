# coding=utf-8
"""Methods to write Dragonfly District Energy Systems (DES) to OpenStudio."""
from __future__ import division
import os

from honeybee_openstudio.openstudio import openstudio_model
from honeybee_openstudio.hvac.standards.schedule import create_constant_schedule_ruleset
from honeybee_openstudio.hvac.standards.central_air_source_heat_pump import \
    create_central_air_source_heat_pump

from dragonfly_energy.des.loop import FifthGenThermalLoop
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
    ground_hx_loop.setMaximumLoopTemperature(design['max_eft'] + 1)  # add 10C
    sizing_plant = ground_hx_loop.sizingPlant()
    sizing_plant.setLoopType('Condenser')
    sizing_plant.setDesignLoopExitTemperature(30.0)
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
    if geojson_dict and 'project' in geojson_dict and \
            'heat_rejection_type' in geojson_dict['project']:
        heat_rejection_type = geojson_dict['project']['heat_rejection_type']
    else:
        heat_rejection_type = 'CoolingTower'
    cooling_stpt = openstudio_model.SetpointManagerScheduled(os_model, hp_high_t_sch)
    gen5_heat_rejection(ground_hx_loop, cooling_stpt, os_model, heat_rejection_type)

    # add supplemental heating to prevent the loop from becoming too cold
    if geojson_dict and 'project' in geojson_dict and \
            'supplemental_heat_type' in geojson_dict['project']:
        supplemental_heat_type = geojson_dict['project']['supplemental_heat_type']
    else:
        supplemental_heat_type = 'Electricity'
    heating_stpt = openstudio_model.SetpointManagerScheduled(os_model, hp_low_t_sch)
    gen5_supplemental_heat(ground_hx_loop, heating_stpt, os_model, supplemental_heat_type)

    # add ground loop pipes
    _gen5_horizontal_pipes(horiz_pipe, soil, ground_hx_loop, os_model, geojson_dict)

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
    sup_wtr_low_temp_c = 12.0
    dsgn_sup_wtr_temp_c = 39.0
    dsgn_sup_wtr_temp_delt_k = 11.0

    sizing_plant = heat_pump_water_loop.sizingPlant()
    sizing_plant.setLoopType('Heating')
    heat_pump_water_loop.setMinimumLoopTemperature(5.0)
    heat_pump_water_loop.setMaximumLoopTemperature(35.0)
    sizing_plant.setDesignLoopExitTemperature(dsgn_sup_wtr_temp_c)
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
    hp_pump = openstudio_model.PumpConstantSpeed(os_model)
    hp_pump.setName('{} Pump'.format(loop_name))
    if not central_pump['pump_flow_rate_autosized']:
        hp_pump.setRatedFlowRate(central_pump['pump_flow_rate'])
    else:
        hp_pump.setRatedPumpHead(179300)
    hp_pump.setPumpControlType('Intermittent')
    hp_pump.addToNode(heat_pump_water_loop.supplyInletNode())

    # create heat rejection equipment and add to the loop
    if geojson_dict and 'project' in geojson_dict and \
            'heat_rejection_type' in geojson_dict['project']:
        heat_rejection_type = geojson_dict['project']['heat_rejection_type']
    else:
        heat_rejection_type = 'CoolingTower'
    cooling_stpt = openstudio_model.SetpointManagerScheduledDualSetpoint(os_model)
    cooling_stpt.setHighSetpointSchedule(hp_high_temp_sch)
    cooling_stpt.setLowSetpointSchedule(hp_low_temp_sch)
    gen5_heat_rejection(heat_pump_water_loop, cooling_stpt, os_model, heat_rejection_type)

    # add supplemental heating to prevent the loop from becoming too cold
    if geojson_dict and 'project' in geojson_dict and \
            'supplemental_heat_type' in geojson_dict['project']:
        supplemental_heat_type = geojson_dict['project']['supplemental_heat_type']
    else:
        supplemental_heat_type = 'Electricity'
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
    _gen5_horizontal_pipes(horiz_pipe, soil, heat_pump_water_loop, os_model, geojson_dict)

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
        if heat_rejection_type in ('CoolingTower', 'CoolingTowerTwoSpeed'):
            cooling_equipment = openstudio_model.CoolingTowerTwoSpeed(os_model)
            cooling_equipment.setName('{} CoolingTowerTwoSpeed'.format(loop_name))
            setpoint_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
        elif heat_rejection_type == 'CoolingTowerSingleSpeed':
            cooling_equipment = openstudio_model.CoolingTowerSingleSpeed(os_model)
            cooling_equipment.setName('{} CoolingTowerSingleSpeed'.format(loop_name))
            setpoint_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
        elif heat_rejection_type == 'CoolingTowerVariableSpeed':
            cooling_equipment = openstudio_model.CoolingTowerVariableSpeed(os_model)
            cooling_equipment.setName('{} CoolingTowerVariableSpeed'.format(loop_name))
            setpoint_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
        elif heat_rejection_type in ('FluidCooler', 'FluidCoolerSingleSpeed'):
            cooling_equipment = openstudio_model.FluidCoolerSingleSpeed(os_model)
            cooling_equipment.setName('{} FluidCoolerSingleSpeed'.format(loop_name))
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            cooling_equipment.autosizeDesignWaterFlowRate()
            cooling_equipment.autosizeDesignAirFlowRate()
        elif heat_rejection_type == 'FluidCoolerTwoSpeed':
            cooling_equipment = openstudio_model.FluidCoolerTwoSpeed(os_model)
            cooling_equipment.setName('{} FluidCoolerTwoSpeed'.format(loop_name))
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            cooling_equipment.autosizeDesignWaterFlowRate()
            cooling_equipment.autosizeHighFanSpeedAirFlowRate()
            cooling_equipment.autosizeLowFanSpeedAirFlowRate()
        elif heat_rejection_type in ('EvaporativeFluidCooler', 'EvaporativeFluidCoolerSingleSpeed'):
            cooling_equipment = openstudio_model.EvaporativeFluidCoolerSingleSpeed(os_model)
            cooling_equipment.setName('{} EvaporativeFluidCoolerSingleSpeed'.format(loop_name))
            cooling_equipment.setDesignSprayWaterFlowRate(0.002208)
            cooling_equipment.setPerformanceInputMethod(fc_size_type)
            setpoint_manager.setName('{} Fluid Cooler Setpoint'.format(loop_name))
        elif heat_rejection_type == 'EvaporativeFluidCoolerTwoSpeed':
            cooling_equipment = openstudio_model.EvaporativeFluidCoolerTwoSpeed(os_model)
            cooling_equipment.setName('{} EvaporativeFluidCoolerTwoSpeed'.format(loop_name))
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
            * AirSourceHeatPump
            * NaturalGas
            * DistrictHeat
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
    elif supplemental_heat_type in ('AirSourceHeatPump', 'ASHP'):
        name = '{} Supplemental ASHP'.format(loop_name)
        heating_equipment = create_central_air_source_heat_pump(os_model, heat_pump_loop, name)
        setpoint_manager.setName('{} Supplemental ASHP Setpoint'.format(loop_name))
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


def _gen5_horizontal_pipes(horiz_pipe, soil, heat_pump_loop, os_model, geojson_dict=None):
    """Create pipes to account for losses in a fifth generation thermal loop."""
    # add ground loop pipes
    if geojson_dict is None:  # add adiabatic pipes
        supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
        demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    else:  # add outdoor pipes
        # create the pipe material
        pipe_thickness = horiz_pipe['hydraulic_diameter'] / horiz_pipe['diameter_ratio']
        pipe_mat = openstudio_model.StandardOpaqueMaterial(os_model)
        pipe_mat.setName('Horizontal Pipe HDPE')
        pipe_mat.setThickness(pipe_thickness)
        pipe_mat.setConductivity(0.5)
        pipe_mat.setDensity(950.0)
        pipe_mat.setSpecificHeat(2000.0)
        pipe_mat.setRoughness('MediumRough')
        # create the insulation material
        insulation = openstudio_model.StandardOpaqueMaterial(os_model)
        insulation.setName('Horizontal Pipe Insulation')
        insulation.setThickness(horiz_pipe['insulation_thickness'])
        insulation.setConductivity(horiz_pipe['insulation_conductivity'])
        insulation.setDensity(43.0)
        insulation.setSpecificHeat(1210.0)
        insulation.setRoughness('MediumRough')
        # create the soil material
        soil_mat = openstudio_model.StandardOpaqueMaterial(os_model)
        soil_mat.setName('Buried Pipe Soil')
        soil_mat.setThickness(horiz_pipe['buried_depth'])
        soil_mat.setConductivity(soil['conductivity'])
        soil_mat.setDensity(1250.0)
        soil_mat.setSpecificHeat(soil['rho_cp'] / 1250.0)
        soil_mat.setRoughness('MediumRough')
        # bring everything together into a pipe construction
        pipe_con = openstudio_model.Construction(os_model)
        pipe_con.setName('Horizontal Pipe Construction')
        os_materials = openstudio_model.MaterialVector()
        for os_material in (soil_mat, insulation, pipe_mat):
            try:
                os_materials.append(os_material)
            except AttributeError:  # using OpenStudio .NET bindings
                os_materials.Add(os_material)
        pipe_con.setLayers(os_materials)

        # deserialize the ThermalConnectors to get their lengths
        loop = FifthGenThermalLoop.from_geojson_dict(geojson_dict)
        total_length = 0
        for connector in loop.connectors:
            total_length += connector.geometry.length

        # apply all properties to the pipes
        supply_outlet_pipe = openstudio_model.PipeOutdoor(os_model)
        demand_outlet_pipe = openstudio_model.PipeOutdoor(os_model)
        for os_pipe in (supply_outlet_pipe, demand_outlet_pipe):
            os_pipe.setConstruction(pipe_con)
            os_pipe.setPipeInsideDiameter(horiz_pipe['hydraulic_diameter'])
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


def gen4_des_to_openstudio(des_dict, os_model):
    """Convert a dictionary of a fourth_generation district_system to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
    """
    return
