"""Test the CLI commands"""
import os
from click.testing import CliRunner

from ladybug.futil import nukedir
from honeybee_energy.result.err import Err

from dragonfly_openstudio.cli.translate import system_to_osm_cli
from dragonfly_openstudio.cli.simulate import simulate_system_cli


def test_system_to_osm():
    runner = CliRunner()
    input_system = './tests/assets/small_ghe/system_params.json'
    output_osm = './tests/assets/small_ghe/system.osm'
    output_idf = './tests/assets/small_ghe/system.idf'

    in_args = [input_system, '--osm-file', output_osm, '--idf-file', output_idf]
    result = runner.invoke(system_to_osm_cli, in_args)
    assert result.exit_code == 0

    assert os.path.isfile(output_osm)
    assert os.path.isfile(output_idf)
    os.remove(output_osm)
    os.remove(output_idf)


def test_simulate_system():
    runner = CliRunner()
    input_system = './tests/assets/small_ghe/system_params.json'
    output_folder = './tests/assets/small_ghe/simulation'
    output_log = './tests/assets/small_ghe/simulation/sim.log'

    in_args = [input_system, '--folder', output_folder, '--log-file', output_log]
    result = runner.invoke(simulate_system_cli, in_args)
    assert result.exit_code == 0

    assert os.path.isdir(output_folder)
    with open(output_log, 'r') as rf:
        out_files = rf.read().split('\n')
    for f in out_files:
        assert os.path.isfile(f)
        if f.endswith('.err'):
            err_obj = Err(f)
            assert 'EnergyPlus Completed Successfully' in err_obj.file_contents
    nukedir(output_folder)
