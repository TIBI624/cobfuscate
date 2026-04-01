"""CObfuscate CLI interface."""

import click
import os
from cobfuscate.obfuscator import obfuscate_file, obfuscate_directory
from cobfuscate.errors import CObfuscateError, InvalidInputError


@click.command()
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
def main(input_path, output_path):
    """Obfuscate Python files using CObfuscate.

    INPUT_PATH: File or directory to obfuscate
    OUTPUT_PATH: Output file or directory
    """
    try:
        if os.path.isfile(input_path):
            obfuscate_file(input_path, output_path)
            click.echo(f"Obfuscated: {input_path} -> {output_path}")
        elif os.path.isdir(input_path):
            obfuscate_directory(input_path, output_path)
            click.echo(f"Obfuscated directory: {input_path} -> {output_path}")
        else:
            raise InvalidInputError(f"Invalid input path: {input_path}")
    except CObfuscateError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()