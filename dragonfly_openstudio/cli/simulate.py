"""honeybee energy simulation running commands."""
import click
import sys
import os
import logging
import json

from ladybug.commandutil import process_content_to_output
from honeybee_energy.simulation.parameter import SimulationParameter
from honeybee_energy.run import run_idf
from honeybee_energy.result.err import Err

from honeybee_openstudio.openstudio import openstudio, OSModel
from honeybee_openstudio.simulation import simulation_parameter_to_openstudio, \
    assign_epw_to_model
from dragonfly_openstudio.writer import sys_dict_to_openstudio
from dragonfly_openstudio.util import coincident_peak_design_days

_logger = logging.getLogger(__name__)


@click.group(help='Commands for simulating URBANopt systems in EnergyPlus.')
def simulate():
    pass


@simulate.command('system')
@click.argument('system-file', type=click.Path(
    exists=True, file_okay=True, dir_okay=False, resolve_path=True))
@click.option('--geojson', '-g', help='Full path to an URBANopt feature GeoJSON, '
              'which can be used to further customize the OpenStudio model. When '
              'supplied, the lengths of ThermalConnectors in the loop will be used to '
              'account for pipe heat losses.', default=None, show_default=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False,
                              resolve_path=True))
@click.option('--sim-par-json', '-sp', help='Full path to a honeybee energy '
              'SimulationParameter JSON that describes all of the settings for '
              'the simulation. If None default parameters will be generated.',
              default=None, show_default=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False,
                              resolve_path=True))
@click.option('--folder', '-f', help='Folder on this computer, into which the IDF '
              'and result files will be written. If None, the files will be output '
              'to a des_energyplus folder in the same directory as the system file.',
              default=None, show_default=True,
              type=click.Path(file_okay=False, dir_okay=True, resolve_path=True))
@click.option('--log-file', '-log', help='Optional log file to output the paths of the '
              'generated files (osm, idf, sql, rdd, html, err) if successfully'
              ' created. By default the list will be printed out to stdout',
              type=click.File('w'), default='-', show_default=True)
def simulate_system_cli(system_file, geojson, sim_par_json, folder, log_file):
    """Simulate an URBANopt DES system in EnergyPlus.

    \b
    Args:
        system_file: Path to an URBANopt system parameter file to be simulated
            in EnergyPlus. Note that all file paths within the system parameter
            must be valid, including the path to the weather file, which will
            be used for simulation.
    """
    try:
        simulate_system(system_file, geojson, sim_par_json, folder, log_file)
    except Exception as e:
        _logger.exception('System simulation failed.\n{}'.format(e))
        sys.exit(1)
    else:
        sys.exit(0)


def simulate_system(
    system_file, geojson=None, sim_par_json=None, folder=None, log_file=None
):
    """Simulate an URBANopt DES system in EnergyPlus.

    Args:
        system_file: Path to an URBANopt system parameter file to be simulated
            in EnergyPlus. Note that all file paths within the system parameter
            must be valid, including the path to the weather file, which will
            be used for simulation.
        geojson: An optional URBANopt feature GeoJSON file path, which can
            be used to further customize the OpenStudio model. When supplied,
            the lengths of ThermalConnectors in the loop will be used to
            account for pipe heat losses.
        sim_par_json: Full path to a honeybee energy SimulationParameter JSON that
            describes all of the settings for the simulation. If None, default
            parameters will be generated.
        folder: Folder on this computer, into which the IDF and result files
            will be written. If None, the files will be output to a des_energyplus
            folder in the same directory as the system file.
        log_file: Optional log file to output the paths of the generated
            files (osm, idf, sql, rdd, html, err) if successfully created.
    """
    # initialize the OpenStudio model and load the system parameter file
    os_model = OSModel()
    with open(system_file) as sf:
        sys_dict = json.load(sf)

    # generate default simulation parameters
    if sim_par_json is None:
        sim_par = SimulationParameter()
    else:
        with open(sim_par_json) as json_file:
            data = json.load(json_file)
        sim_par = SimulationParameter.from_dict(data)
    sim_par.output.add_plant_variables()

    # set design days using the coincident peak load
    epw_file = sys_dict['weather'].replace('.mos', '.epw')
    assert os.path.isfile(epw_file), 'The weather file path referenced in the ' \
        'system parameter file was not found: {}'.format(epw_file)
    if len(sim_par.sizing_parameter.design_days) == 0:
        sim_par.sizing_parameter.design_days = coincident_peak_design_days(sys_dict)
    assign_epw_to_model(epw_file, os_model)

    # translate the simulation parameter and the system to an OpenStudio Model
    print('Translating URBANopt system parameter to OpenStudio...')
    simulation_parameter_to_openstudio(sim_par, os_model)
    geojson_dict = None
    if geojson is not None:
        with open(geojson) as json_file:
            geojson_dict = json.load(json_file)
    os_model = sys_dict_to_openstudio(
        sys_dict, geojson_dict=geojson_dict, seed_model=os_model)
    print('Translation complete!')

    # set up the simulation directory
    directory = folder if folder is not None else \
        os.path.join(os.path.dirname(system_file), 'des_energyplus')
    if not os.path.isdir(directory):
        os.makedirs(directory)

    # write the OSM and IDF
    osm = os.path.abspath(os.path.join(directory, 'in.osm'))
    os_model.save(osm, overwrite=True)
    idf = os.path.abspath(os.path.join(directory, 'in.idf'))
    idf_translator = openstudio.energyplus.ForwardTranslator()
    workspace = idf_translator.translateModel(os_model)
    workspace.save(idf, overwrite=True)

    # run the simulation
    gen_files = [osm, idf]
    sql, _, rdd, html, err = run_idf(idf, epw_file)
    if err is not None and os.path.isfile(err):
        gen_files.extend([sql, rdd, html, err])
    else:
        raise Exception('Running EnergyPlus failed.')

    # parse the error log and return the generated files
    err_obj = Err(err)
    for error in err_obj.fatal_errors:
        raise Exception(error)
    return process_content_to_output('\n'.join(gen_files), log_file)
