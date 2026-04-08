# coding=utf-8
"""Methods to write Dragonfly Energy Transfer Stations (ETS) to OpenStudio."""
from __future__ import division

from honeybee_energy.schedule.fixedinterval import ScheduleFixedInterval
from honeybee_energy.lib.scheduletypelimits import fractional, power
from honeybee_openstudio.openstudio import openstudio_model
from honeybee_openstudio.schedule import schedule_fixed_interval_to_openstudio
from honeybee_openstudio.hvac.standards.schedule import create_constant_schedule_ruleset

from .util import modelica_loads


def heat_pump_ets_to_openstudio(building_dict, hp_loop, os_model):
    """Convert a dictionary of building with fifth_gen_ets_parameters to OpenStudio.

    Args:
        building_dict: A building dictionary with a "Fifth Gen Heat Pump"
            ets_model to be converted into building-side thermal loops.
        hp_loop: The ambient heat pump condenser loop to which the buildings
            will be added.
        os_model: The OpenStudio Model to which the loops will be added.
    """
    # get the various sub-objects of the main dictionary
    ets_dict = building_dict['fifth_gen_ets_parameters']
    load_dict = building_dict['load_model_parameters']['time_series']
    bldg_id = building_dict['geojson_id']

    # GET LOADS
    # parse the loads from the .mos file
    _, cooling, heating, shw = modelica_loads(load_dict['filepath'])
    peak_cooling = min(cooling)
    peak_cooling = peak_cooling if peak_cooling < 0 else 0
    peak_heating = max(heating)
    peak_heating = peak_heating if peak_heating > 0 else 0
    peak_shw = max(shw)
    peak_shw = peak_shw if peak_shw > 0 else 0
    pump_head = ets_dict['ets_pump_head']

    # CHILLED WATER LOOP
    chw_loop = None
    if peak_cooling != 0:
        # create the loop
        chw_temp = ets_dict['chilled_water_supply_temp']
        chw_loop = building_chw_loop(bldg_id, cooling, chw_temp, os_model, pump_head)
        # add the heat pump
        chw_hp = openstudio_model.HeatPumpWaterToWaterEquationFitCooling(os_model)
        chw_hp.setReferenceCoefficientofPerformance(ets_dict['cop_heat_pump_heating'])
        chw_hp.setName('{} Cooling Heat Pump'.format(bldg_id))
        chw_loop.addSupplyBranchForComponent(chw_hp)
        hp_loop.addDemandBranchForComponent(chw_hp)

    # HEATING WATER LOOP
    hw_loop = None
    if peak_heating != 0:
        # create the loop
        hw_temp = ets_dict['heating_water_supply_temp']
        hw_loop = building_hw_loop(bldg_id, heating, hw_temp, os_model, pump_head)
        # add supply side equipment to the heating water loop
        hw_hp = openstudio_model.HeatPumpWaterToWaterEquationFitHeating(os_model)
        hw_hp.setReferenceCoefficientofPerformance(ets_dict['cop_heat_pump_heating'])
        hw_hp.setName('{} Heating Heat Pump'.format(bldg_id))
        hw_loop.addSupplyBranchForComponent(hw_hp)
        hp_loop.addDemandBranchForComponent(hw_hp)

    # SHW LOOP
    shw_loop = None
    if peak_shw != 0:
        # create the loop
        shw_temp = ets_dict['hot_water_supply_temp']
        shw_loop = building_shw_loop(bldg_id, shw, shw_temp, os_model, pump_head)
        # add supply side equipment to the heating water loop
        shw_hp = openstudio_model.HeatPumpWaterToWaterEquationFitHeating(os_model)
        shw_hp.setReferenceCoefficientofPerformance(ets_dict['cop_heat_pump_hot_water'])
        shw_hp.setName('{} SHW Heat Pump'.format(bldg_id))
        shw_loop.addSupplyBranchForComponent(shw_hp)
        hp_loop.addDemandBranchForComponent(shw_hp)

    return chw_loop, hw_loop, shw_loop


def heat_exchanger_ets_to_openstudio(building_dict, os_model):
    """Convert a dictionary of building with ets_indirect_parameters to OpenStudio.

    Args:
        building_dict: A building dictionary with a "Indirect Heating and Cooling"
            ets_model to be converted into building-side thermal loops.
        os_model: The OpenStudio Model to which the loops will be added.
    """
    return


def building_chw_loop(bldg_id, cooling, chw_temp, os_model, pump_pressure=None):
    """Get a building-side chilled water loop with pump, setpoint manager, and loads.

    Args:
        bldg_id: The identifier of the Building for the chilled water loop.
        cooling: An array of timeseries values for the annual cooling load in Watts.
        chw_temp: The temperature of the chilled water loop in C.
        os_model: The OpenStudio Model to which the loop will be added.
        pump_pressure: An optional value for the pump head pressure in Pa.
    """
    # initialize the loop and set the temperature
    chw_loop = openstudio_model.PlantLoop(os_model)
    chw_loop.setName('{} Chilled Water Loop'.format(bldg_id))
    chw_loop.setMaximumLoopTemperature(40.0)
    chw_sizing_plant = chw_loop.sizingPlant()
    chw_sizing_plant.setDesignLoopExitTemperature(chw_temp)
    chw_sizing_plant.setLoopDesignTemperatureDifference(4.0)
    chw_sizing_plant.setLoopType('Cooling')
    chw_temp_sch = create_constant_schedule_ruleset(
        os_model, chw_temp, schedule_type_limit='Temperature',
        name='{} Temp - {}C'.format(chw_loop.nameString(), int(chw_temp)))
    chw_stpt_manager = openstudio_model.SetpointManagerScheduled(os_model, chw_temp_sch)
    chw_stpt_manager.setName('{} Setpoint Manager'.format(chw_loop.nameString()))
    chw_stpt_manager.addToNode(chw_loop.supplyOutletNode())

    # add a pump for the loop
    chw_pump = openstudio_model.PumpVariableSpeed(os_model)
    chw_pump.setName('{} Pump'.format(chw_loop.nameString()))
    if pump_pressure is not None:
        chw_pump.setRatedPumpHead(pump_pressure)
    chw_pump.setMotorEfficiency(0.9)
    chw_pump.setPumpControlType('Intermittent')
    chw_pump.addToNode(chw_loop.supplyInletNode())

    # set the cooling load schedule
    timestep = len(cooling) / 8760
    peak_cool = min(cooling)
    load_sch_id = '{} Cooling Load Sch - {}kW'.format(bldg_id, int(abs(peak_cool) / 1000))
    load_sch = ScheduleFixedInterval(load_sch_id, cooling, power, timestep)
    os_load_sch = schedule_fixed_interval_to_openstudio(load_sch, os_model)

    # set the flow rate schedule
    peak_flow = (abs(peak_cool) / (4184000 * 3)) * 1.15  # Water DeltaT of 3C * sizing factor
    flow_rate = [abs(cool_i / peak_cool) for cool_i in cooling]
    flow_sch_id = '{} Cooling Flow Sch - {}L/s'.format(bldg_id, int(peak_flow * 1000))
    flow_sch = ScheduleFixedInterval(flow_sch_id, flow_rate, fractional, timestep)
    os_flow_sch = schedule_fixed_interval_to_openstudio(flow_sch, os_model)
    chw_pump.setRatedFlowRate(peak_flow * 1.1)

    # add the building loads to the supply side
    os_load = openstudio_model.LoadProfilePlant(os_model, os_load_sch, os_flow_sch)
    os_load.setPeakFlowRate(peak_flow)
    os_load.setName('{} Cooling Load'.format(bldg_id))
    chw_loop.addDemandBranchForComponent(os_load)

    return chw_loop


def building_hw_loop(bldg_id, heating, hw_temp, os_model, pump_pressure=None):
    """Get a building-side heating water loop with pump, setpoint manager, and loads.

    Args:
        bldg_id: The identifier of the Building for the heating water loop.
        heating: An array of timeseries values for the annual heating load in Watts.
        hw_temp: The temperature of the heating water loop in C.
        os_model: The OpenStudio Model to which the loop will be added.
        pump_pressure: An optional value for the pump head pressure in Pa.
    """
    # create the heating water loop at the specified temperature
    hw_loop = openstudio_model.PlantLoop(os_model)
    hw_loop.setName('{} Heating Water Loop'.format(bldg_id))
    hw_sizing_plant = hw_loop.sizingPlant()
    hw_sizing_plant.setDesignLoopExitTemperature(hw_temp + 11.0)
    hw_sizing_plant.setLoopDesignTemperatureDifference(11.0)
    hw_sizing_plant.setLoopType('Heating')
    hw_temp_sch = create_constant_schedule_ruleset(
        os_model, hw_temp, schedule_type_limit='Temperature',
        name='{} Temp - {}C'.format(hw_loop.nameString(), int(hw_temp)))
    hw_stpt_manager = openstudio_model.SetpointManagerScheduled(os_model, hw_temp_sch)
    hw_stpt_manager.setName('{} Setpoint Manager'.format(hw_loop.nameString()))
    hw_stpt_manager.addToNode(hw_loop.supplyOutletNode())

    # add a pump for the loop
    hw_pump = openstudio_model.PumpVariableSpeed(os_model)
    hw_pump.setName('{} Pump'.format(hw_loop.nameString()))
    if pump_pressure is not None:
        hw_pump.setRatedPumpHead(pump_pressure)
    hw_pump.setMotorEfficiency(0.9)
    hw_pump.setPumpControlType('Intermittent')
    hw_pump.addToNode(hw_loop.supplyInletNode())

    # set the heating load schedule
    timestep = len(heating) / 8760
    peak_heat = max(heating)
    load_sch_id = '{} Heating Load Sch - {}kW'.format(bldg_id, int(abs(peak_heat) / 1000))
    load_sch = ScheduleFixedInterval(load_sch_id, heating, power, timestep)
    os_load_sch = schedule_fixed_interval_to_openstudio(load_sch, os_model)

    # set the flow rate schedule
    peak_flow = (abs(peak_heat) / (4184000 * 2)) * 1.25  # Water DeltaT of 2C * sizing factor
    flow_rate = [abs(heat_i) / peak_heat for heat_i in heating]
    flow_sch_id = '{} Heating Flow Sch - {}L/s'.format(bldg_id, int(peak_flow * 1000))
    flow_sch = ScheduleFixedInterval(flow_sch_id, flow_rate, fractional, timestep)
    os_flow_sch = schedule_fixed_interval_to_openstudio(flow_sch, os_model)
    hw_pump.setRatedFlowRate(peak_flow * 1.1)

    # add the building loads to the supply side
    os_load = openstudio_model.LoadProfilePlant(os_model, os_load_sch, os_flow_sch)
    os_load.setPeakFlowRate(peak_flow)
    os_load.setName('{} Heating Load'.format(bldg_id))
    hw_loop.addDemandBranchForComponent(os_load)

    return hw_loop


def building_shw_loop(bldg_id, shw, shw_temp, os_model, pump_pressure=None):
    """Get a building-side service hot water loop with pump, setpoint manager, and loads.

    Args:
        bldg_id: The identifier of the Building for the shw water loop.
        shw: An array of timeseries values for the annual shw load in Watts.
        shw_temp: The temperature of the shw loop in C.
        os_model: The OpenStudio Model to which the loop will be added.
        pump_pressure: An optional value for the pump head pressure in Pa.
    """
    # create the SHW loop at the specified temperature
    shw_loop = openstudio_model.PlantLoop(os_model)
    shw_loop.setName('{} SHW Loop'.format(bldg_id))
    shw_sizing_plant = shw_loop.sizingPlant()
    shw_sizing_plant.setDesignLoopExitTemperature(shw_temp + 11.0)
    shw_sizing_plant.setLoopDesignTemperatureDifference(11.0)
    shw_sizing_plant.setLoopType('Heating')
    shw_temp_sch = create_constant_schedule_ruleset(
        os_model, shw_temp, schedule_type_limit='Temperature',
        name='{} Temp - {}C'.format(shw_loop.nameString(), int(shw_temp)))
    shw_stpt_manager = openstudio_model.SetpointManagerScheduled(os_model, shw_temp_sch)
    shw_stpt_manager.setName('{} Setpoint Manager'.format(shw_loop.nameString()))
    shw_stpt_manager.addToNode(shw_loop.supplyOutletNode())

    # add a pump for the loop
    shw_pump = openstudio_model.PumpVariableSpeed(os_model)
    shw_pump.setName('{} Pump'.format(shw_loop.nameString()))
    if pump_pressure is not None:
        shw_pump.setRatedPumpHead(pump_pressure)
    shw_pump.setMotorEfficiency(0.9)
    shw_pump.setPumpControlType('Intermittent')
    shw_pump.addToNode(shw_loop.supplyInletNode())

    # set the shw load schedule
    timestep = len(shw) / 8760
    peak_heat = max(shw)
    load_sch_id = '{} SHW Load Sch - {}kW'.format(bldg_id, int(abs(peak_heat) / 1000))
    load_sch = ScheduleFixedInterval(load_sch_id, shw, power, timestep)
    os_load_sch = schedule_fixed_interval_to_openstudio(load_sch, os_model)

    # set the flow rate schedule
    peak_flow = (abs(peak_heat) / (4184000 * 2)) * 1.25  # Water DeltaT of 2C * sizing factor
    flow_rate = [abs(heat_i) / peak_heat for heat_i in shw]
    flow_sch_id = '{} SHW Flow Sch - {}L/s'.format(bldg_id, int(peak_flow * 1000))
    flow_sch = ScheduleFixedInterval(flow_sch_id, flow_rate, fractional, timestep)
    os_flow_sch = schedule_fixed_interval_to_openstudio(flow_sch, os_model)
    shw_pump.setRatedFlowRate(peak_flow * 1.1)

    # add the building loads to the supply side
    os_load = openstudio_model.LoadProfilePlant(os_model, os_load_sch, os_flow_sch)
    os_load.setPeakFlowRate(peak_flow)
    os_load.setName('{} SHW Load'.format(bldg_id))
    shw_loop.addDemandBranchForComponent(os_load)

    return shw_loop
