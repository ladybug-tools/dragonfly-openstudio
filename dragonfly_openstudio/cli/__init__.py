"""dragonfly-openstudio commands which will be added to the dragonfly cli"""
import click
from dragonfly.cli import main


@click.group(help='dragonfly openstudio commands.')
@click.version_option()
def openstudio():
    pass


# add openstudio sub-commands
main.add_command(openstudio)
