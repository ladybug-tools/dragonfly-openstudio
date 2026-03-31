"""dragonfly openstudio translation commands."""
import click
import sys
import os
import logging
import json

from ladybug.commandutil import process_content_to_output
from ladybug.epw import EPW
from honeybee_energy.simulation.parameter import SimulationParameter

from honeybee_openstudio.openstudio import openstudio, OSModel
from honeybee_openstudio.simulation import simulation_parameter_to_openstudio, \
    assign_epw_to_model
from dragonfly_openstudio.writer import sys_dict_to_openstudio


_logger = logging.getLogger(__name__)


@click.group(help='Commands for translating Dragonfly JSON files to/from OSM/IDF.')
def translate():
    pass


@translate.command('system-to-osm')
@click.argument('system-file', type=click.Path(
    exists=True, file_okay=True, dir_okay=False, resolve_path=True))
@click.option('--sim-par-json', '-sp', help='Full path to a honeybee energy '
              'SimulationParameter JSON that describes all of the settings for '
              'the simulation. If None default parameters will be generated.',
              default=None, show_default=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False,
                              resolve_path=True))
@click.option('--osm-file', '-osm', help='Optional path where the OSM will be written.',
              type=str, default=None, show_default=True)
@click.option('--idf-file', '-idf', help='Optional path where the IDF will be written.',
              type=str, default=None, show_default=True)
@click.option('--log-file', '-log', help='Optional log file to output the paths to the '
              'generated OSM and IDF files if they were successfully created. '
              'By default this will be printed out to stdout',
              type=click.File('w'), default='-', show_default=True)
def system_to_osm_cli(system_file, sim_par_json, osm_file, idf_file, log_file):
    """Translate an URBANopt system parameter to an OpenStudio Model.

    \b
    Args:
        system_file: Path to an URBANopt system parameter file to be translated
            to an OpenStudio model.
    """
    try:
        system_to_osm(system_file, sim_par_json, osm_file, idf_file, log_file)
    except Exception as e:
        _logger.exception('System translation failed.\n{}'.format(e))
        sys.exit(1)
    else:
        sys.exit(0)


def system_to_osm(
    system_file, sim_par_json=None, osm_file=None, idf_file=None, log_file=None
):
    """Translate an URBANopt system parameter to an OpenStudio Model.

    Args:
        system_file: Path to an URBANopt system parameter file to be translated
            to an OpenStudio model.
        sim_par_json: Full path to a honeybee energy SimulationParameter JSON that
            describes all of the settings for the simulation. If None, default
            parameters will be generated.
        osm_file: Optional path where the OSM will be output.
        idf_file: Optional path where the IDF will be output.
        log_file: Optional log file to output the paths to the generated OSM and
            IDF files if they were successfully created. By default this string
            will be returned from this method.
    """
    # initialize the OpenStudio model that will hold everything
    os_model = OSModel()
    # generate default simulation parameters
    if sim_par_json is None:
        sim_par = SimulationParameter()
        sim_par.output.add_hvac_energy_use()
    else:
        with open(sim_par_json) as json_file:
            data = json.load(json_file)
        sim_par = SimulationParameter.from_dict(data)

    # load the system parameter file to a dictionary and get the weather file
    with open(system_file) as sf:
        sys_dict = json.load(sf)
    epw_file = sys_dict['weather'].replace('.mos', '.epw')

    # set two design days using the EPW (to be coordinated with coincident peak load)
    if len(sim_par.sizing_parameter.design_days) == 0:
        assert os.path.isfile(epw_file), 'The weather file path found in the ' \
            'system parameter file was not found: {}'.format(epw_file)
        epw_obj = EPW(epw_file)
        des_days = [epw_obj.approximate_design_day('WinterDesignDay'),
                    epw_obj.approximate_design_day('SummerDesignDay')]
        sim_par.sizing_parameter.design_days = des_days
        set_cz = True if sim_par.sizing_parameter.climate_zone is None else False
        assign_epw_to_model(epw_file, os_model, set_cz)

    # translate the simulation parameter and model to an OpenStudio Model
    simulation_parameter_to_openstudio(sim_par, os_model)

    # translate the system parameter to OpenStudio
    os_model = sys_dict_to_openstudio(sys_dict, os_model)
    gen_files = []

    # write the OpenStudio Model if specified
    if osm_file is not None:
        osm = os.path.abspath(osm_file)
        os_model.save(osm, overwrite=True)
        gen_files.append(osm)

    # write the IDF if specified
    if idf_file is not None:
        idf = os.path.abspath(idf_file)
        idf_translator = openstudio.energyplus.ForwardTranslator()
        workspace = idf_translator.translateModel(os_model)
        workspace.save(idf, overwrite=True)
        gen_files.append(idf)

    return process_content_to_output(json.dumps(gen_files, indent=4), log_file)
