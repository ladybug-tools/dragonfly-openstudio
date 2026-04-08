"""dragonfly openstudio translation commands."""
import click
import sys
import os
import logging
import json

from ladybug.commandutil import process_content_to_output
from honeybee_energy.simulation.parameter import SimulationParameter

from honeybee_openstudio.openstudio import openstudio, OSModel
from honeybee_openstudio.simulation import simulation_parameter_to_openstudio, \
    assign_epw_to_model
from dragonfly_openstudio.writer import sys_dict_to_openstudio
from dragonfly_openstudio.util import coincident_peak_design_days


_logger = logging.getLogger(__name__)


@click.group(help='Commands for translating URBANopt systems to OSM/IDF.')
def translate():
    pass


@translate.command('system-to-osm')
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
@click.option('--osm-file', '-osm', help='Optional path where the OSM will be written.',
              type=str, default=None, show_default=True)
@click.option('--idf-file', '-idf', help='Optional path where the IDF will be written.',
              type=str, default=None, show_default=True)
@click.option('--log-file', '-log', help='Optional log file to output the paths to the '
              'generated OSM and IDF files if they were successfully created. '
              'By default this will be printed out to stdout',
              type=click.File('w'), default='-', show_default=True)
def system_to_osm_cli(system_file, geojson, sim_par_json, osm_file, idf_file, log_file):
    """Translate an URBANopt system parameter to an OpenStudio Model.

    \b
    Args:
        system_file: Path to an URBANopt system parameter file to be translated
            to an OpenStudio model.
    """
    try:
        system_to_osm(system_file, geojson, sim_par_json, osm_file, idf_file, log_file)
    except Exception as e:
        _logger.exception('System translation failed.\n{}'.format(e))
        sys.exit(1)
    else:
        sys.exit(0)


def system_to_osm(
    system_file, geojson=None, sim_par_json=None, osm_file=None, idf_file=None,
    log_file=None
):
    """Translate an URBANopt system parameter to an OpenStudio Model.

    Args:
        system_file: Path to an URBANopt system parameter file to be translated
            to an OpenStudio model.
        geojson: An optional URBANopt feature GeoJSON file path, which can
            be used to further customize the OpenStudio model. When supplied,
            the lengths of ThermalConnectors in the loop will be used to
            account for pipe heat losses.
        sim_par_json: Full path to a honeybee energy SimulationParameter JSON that
            describes all of the settings for the simulation. If None, default
            parameters will be generated.
        osm_file: Optional path where the OSM will be output.
        idf_file: Optional path where the IDF will be output.
        log_file: Optional log file to output the paths to the generated OSM and
            IDF files if they were successfully created. By default this string
            will be returned from this method.
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
    simulation_parameter_to_openstudio(sim_par, os_model)
    geojson_dict = None
    if geojson is not None:
        with open(geojson) as json_file:
            geojson_dict = json.load(json_file)
    os_model = sys_dict_to_openstudio(
        sys_dict, geojson_dict=geojson_dict, seed_model=os_model)
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

    return process_content_to_output('\n'.join(gen_files), log_file)
