# -*- coding: utf-8 -*-

"""
Entry point for the CLI
"""

import click
import os
import sys
import logging

work_dir = os.getcwd()
dir_name, file_name = os.path.split(os.path.abspath(sys.argv[0]))
os.chdir(dir_name)
os.chdir('../../..')
sys.path.insert(0, os.getcwd())
os.chdir(work_dir)

from tcfcli.cmds.cli import __version__
from tcfcli.cmds.deploy.cli import deploy
from tcfcli.cmds.local.cli import local
from tcfcli.cmds.init.cli import init
from tcfcli.cmds.validate.cli import validate
from tcfcli.cmds.configure.cli import configure
from tcfcli.cmds.native.cli import native
from tcfcli.cmds.logs.cli import logs
from tcfcli.cmds.list.cli import list
from tcfcli.cmds.delete.cli import delete

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')


@click.group(context_settings=dict(help_option_names=['--help']))
@click.version_option(version=__version__, prog_name="SCF CLI")
def cli():
    '''
        \b
        If you want to study how to use SCF CLI, you could refer to documentation.
          * Documentation https://cloud.tencent.com/document/product/583/33445?from=cli
        \b
        If you have any questions, you could give us issues on Github.
          * Github  https://github.com/tencentyun/scfcl
    '''
    pass


"""
Register commands into cli group"
"""

cli.add_command(configure)
cli.add_command(init)
cli.add_command(deploy)
cli.add_command(native)
cli.add_command(validate)
cli.add_command(logs)
cli.add_command(local)

cli.add_command(delete)
cli.add_command(list)

if __name__ == "__main__":
    cli()
