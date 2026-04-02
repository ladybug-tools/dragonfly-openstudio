# coding=utf-8
"""Methods to write Dragonfly District Energy Systems (DES) to OpenStudio."""
from __future__ import division
import os

from honeybee_openstudio.openstudio import openstudio_model
from honeybee_openstudio.hvac.standards.schedule import create_constant_schedule_ruleset
from honeybee_openstudio.hvac.standards.central_air_source_heat_pump import \
    create_central_air_source_heat_pump
from honeybee_openstudio.hvac.standards.boiler_hot_water import create_boiler_hot_water

from dragonfly_energy.des.ghe import GroundHeatExchanger


def ghe_des_to_openstudio(des_dict, os_model):
    """Convert a dictionary of a district_system with ghe_parameters to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
    """
    # get the various sub-objects of the main dictionary
    des_dict = des_dict['fifth_generation']
    central_pump = des_dict['central_pump_parameters']
    soil = des_dict['soil']
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

    # add a cooling tower to prevent the loop from overheating during peak
    cooling_equipment_stpt_manager = \
        openstudio_model.SetpointManagerScheduled(os_model, hp_high_t_sch)
    cooling_equipment = openstudio_model.CoolingTowerVariableSpeed(os_model)
    cooling_equipment.setName('{} CoolingTowerVariableSpeed'.format(loop_name))
    ground_hx_loop.addSupplyBranchForComponent(cooling_equipment)
    cooling_equipment_stpt_manager.setName('{} Cooling Tower Setpoint'.format(loop_name))
    equip_out_node = cooling_equipment.outletModelObject().get().to_Node().get()
    cooling_equipment_stpt_manager.addToNode(equip_out_node)

    # add an electric boiler to prevent the loop from becoming too cold
    heating_equipment_stpt_manager = \
        openstudio_model.SetpointManagerScheduled(os_model, hp_low_t_sch)
    heating_equipment = openstudio_model.BoilerHotWater(os_model)
    heating_equipment.setNominalThermalEfficiency(1.0)
    heating_equipment.setFuelType('Electricity')
    heating_equipment.setName('{} Supplemental Boiler'.format(loop_name))
    ground_hx_loop.addSupplyBranchForComponent(heating_equipment)
    heating_equipment_stpt_manager.setName('{} Boiler Setpoint'.format(loop_name))
    equip_out_node = heating_equipment.outletModelObject().get().to_Node().get()
    heating_equipment_stpt_manager.addToNode(equip_out_node)

    # add ground loop pipes
    # TODO: Consider using PipeOutdoor with lengths derived from thermal connectors
    supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_outlet_pipe.setName('{} Supply Outlet'.format(loop_name))
    supply_outlet_pipe.addToNode(ground_hx_loop.supplyOutletNode())

    demand_inlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_inlet_pipe.setName('{} Demand Inlet'.format(loop_name))
    demand_inlet_pipe.addToNode(ground_hx_loop.demandInletNode())

    demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_outlet_pipe.setName('{} Demand Outlet'.format(loop_name))
    demand_outlet_pipe.addToNode(ground_hx_loop.demandOutletNode())

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


def gen5_des_to_openstudio(
    des_dict, os_model,
    cooling_type='CoolingTowerVariableSpeed', heating_type='Electricity'
):
    """Convert a dictionary of a fifth_generation district_system to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
        cooling_type: Text for the equipment used to cool the loop when it overheats.
            Choose from the options below. (Default: CoolingTowerVariableSpeed).

            * CoolingTowerSingleSpeed
            * CoolingTowerTwoSpeed
            * CoolingTowerVariableSpeed
            * FluidCooler
            * FluidCoolerSingleSpeed
            * FluidCoolerTwoSpeed
            * EvaporativeFluidCooler
            * EvaporativeFluidCoolerSingleSpeed
            * EvaporativeFluidCoolerTwoSpeed

        heating_type: Text for the equipment used to heat the loop when it requires
            supplemental heating. Choose from the options below. (Default: NaturalGas).

            * Electricity
            * AirSourceHeatPump
            * NaturalGas
            * Propane
            * PropaneGas
            * FuelOilNo1
            * FuelOilNo2

    """
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
    hp_pump.setRatedPumpHead(179300)
    hp_pump.setPumpControlType('Intermittent')
    hp_pump.addToNode(heat_pump_water_loop.supplyInletNode())

    # add setpoint to cooling outlet so correct plant operation scheme is generated
    cooling_equipment_stpt_manager = \
        openstudio_model.SetpointManagerScheduledDualSetpoint(os_model)
    cooling_equipment_stpt_manager.setHighSetpointSchedule(hp_high_temp_sch)
    cooling_equipment_stpt_manager.setLowSetpointSchedule(hp_low_temp_sch)

    # create cooling equipment and add to the loop
    if cooling_type == 'CoolingTowerSingleSpeed':
        cooling_equipment = openstudio_model.CoolingTowerSingleSpeed(os_model)
        cooling_equipment.setName('{} CoolingTowerSingleSpeed'.format(loop_name))
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Cooling Tower Scheduled Dual Setpoint'.format(loop_name))
    elif cooling_type == 'CoolingTowerTwoSpeed':
        cooling_equipment = openstudio_model.CoolingTowerTwoSpeed(os_model)
        cooling_equipment.setName('{} CoolingTowerTwoSpeed'.format(loop_name))
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Cooling Tower Scheduled Dual Setpoint'.format(loop_name))
    elif cooling_type == 'CoolingTowerVariableSpeed':
        cooling_equipment = openstudio_model.CoolingTowerVariableSpeed(os_model)
        cooling_equipment.setName('{} CoolingTowerVariableSpeed'.format(loop_name))
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Cooling Tower Scheduled Dual Setpoint'.format(loop_name))
    elif cooling_type in ('FluidCooler', 'FluidCoolerSingleSpeed'):
        cooling_equipment = openstudio_model.FluidCoolerSingleSpeed(os_model)
        cooling_equipment.setName('{} FluidCoolerSingleSpeed'.format(loop_name))
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Fluid Cooler Scheduled Dual Setpoint'.format(loop_name))
        # Remove hard coded default values
        cooling_equipment.setPerformanceInputMethod(
            'UFactorTimesAreaAndDesignWaterFlowRate')
        cooling_equipment.autosizeDesignWaterFlowRate()
        cooling_equipment.autosizeDesignAirFlowRate()
    elif cooling_type == 'FluidCoolerTwoSpeed':
        cooling_equipment = openstudio_model.FluidCoolerTwoSpeed(os_model)
        cooling_equipment.setName('{} FluidCoolerTwoSpeed'.format(loop_name))
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Fluid Cooler Scheduled Dual Setpoint'.format(loop_name))
        # Remove hard coded default values
        cooling_equipment.setPerformanceInputMethod(
            'UFactorTimesAreaAndDesignWaterFlowRate')
        cooling_equipment.autosizeDesignWaterFlowRate()
        cooling_equipment.autosizeHighFanSpeedAirFlowRate()
        cooling_equipment.autosizeLowFanSpeedAirFlowRate()
    elif cooling_type in ('EvaporativeFluidCooler', 'EvaporativeFluidCoolerSingleSpeed'):
        cooling_equipment = openstudio_model.EvaporativeFluidCoolerSingleSpeed(os_model)
        cooling_equipment.setName(
            '{} EvaporativeFluidCoolerSingleSpeed'.format(loop_name))
        cooling_equipment.setDesignSprayWaterFlowRate(0.002208)  # Based on HighRiseApartment
        cooling_equipment.setPerformanceInputMethod(
            'UFactorTimesAreaAndDesignWaterFlowRate')
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Fluid Cooler Scheduled Dual Setpoint'.format(loop_name))
    elif cooling_type == 'EvaporativeFluidCoolerTwoSpeed':
        cooling_equipment = openstudio_model.EvaporativeFluidCoolerTwoSpeed(os_model)
        cooling_equipment.setName('{} EvaporativeFluidCoolerTwoSpeed'.format(loop_name))
        cooling_equipment.setDesignSprayWaterFlowRate(0.002208)  # Based on HighRiseApartment
        cooling_equipment.setPerformanceInputMethod(
            'UFactorTimesAreaAndDesignWaterFlowRate')
        heat_pump_water_loop.addSupplyBranchForComponent(cooling_equipment)
        cooling_equipment_stpt_manager.setName(
            '{} Fluid Cooler Scheduled Dual Setpoint'.format(loop_name))
    else:
        msg = 'Cooling type "{}" is not a valid option.'.format(cooling_type)
        raise ValueError(msg)
    equip_out_node = cooling_equipment.outletModelObject().get().to_Node().get()
    cooling_equipment_stpt_manager.addToNode(equip_out_node)

    # add setpoint to heating outlet so correct plant operation scheme is generated
    heating_equipment_stpt_manager = \
        openstudio_model.SetpointManagerScheduledDualSetpoint(os_model)
    heating_equipment_stpt_manager.setHighSetpointSchedule(hp_high_temp_sch)
    heating_equipment_stpt_manager.setLowSetpointSchedule(hp_low_temp_sch)

    # create heating equipment and add to the loop
    if heating_type == 'AirSourceHeatPump':
        heating_equipment = create_central_air_source_heat_pump(
            os_model, heat_pump_water_loop)
        heating_equipment_stpt_manager.setName(
            '{} ASHP Scheduled Dual Setpoint'.format(loop_name))
    elif heating_type in ('NaturalGas', 'Electricity', 'Propane',
                          'PropaneGas', 'FuelOilNo1', 'FuelOilNo2'):
        heating_equipment = create_boiler_hot_water(
            os_model, hot_water_loop=heat_pump_water_loop,
            name='{} Supplemental Boiler'.format(loop_name), fuel_type=heating_type,
            flow_mode='ConstantFlow',
            lvg_temp_dsgn_f=86.0, min_plr=0.0, max_plr=1.2, opt_plr=1.0)
        heating_equipment_stpt_manager.setName(
            '{} Boiler Scheduled Dual Setpoint'.format(loop_name))
    else:
        raise ValueError('Boiler fuel type "{}" is not valid'.format(heating_type))
    equip_out_node = heating_equipment.outletModelObject().get().to_Node().get()
    heating_equipment_stpt_manager.addToNode(equip_out_node)

    # add heat pump water loop pipes
    supply_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_bypass_pipe.setName('{} Supply Bypass'.format(loop_name))
    heat_pump_water_loop.addSupplyBranchForComponent(supply_bypass_pipe)

    demand_bypass_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_bypass_pipe.setName('{} Demand Bypass'.format(loop_name))
    heat_pump_water_loop.addDemandBranchForComponent(demand_bypass_pipe)

    supply_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    supply_outlet_pipe.setName('{} Supply Outlet'.format(loop_name))
    supply_outlet_pipe.addToNode(heat_pump_water_loop.supplyOutletNode())

    demand_inlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_inlet_pipe.setName('{} Demand Inlet'.format(loop_name))
    demand_inlet_pipe.addToNode(heat_pump_water_loop.demandInletNode())

    demand_outlet_pipe = openstudio_model.PipeAdiabatic(os_model)
    demand_outlet_pipe.setName('{} Demand Outlet'.format(loop_name))
    demand_outlet_pipe.addToNode(heat_pump_water_loop.demandOutletNode())

    return heat_pump_water_loop


def gen4_des_to_openstudio(des_dict, os_model):
    """Convert a dictionary of a fourth_generation district_system to OpenStudio.

    Args:
        des_dict: A district_system dictionary to be converted into thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
    """
    return
