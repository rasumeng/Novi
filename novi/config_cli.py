"""novi config show|set|reset

Every read and write goes through the Configuration Framework — the single
persistence authority. There is no direct TOML mutation here.
"""

from .configuration.bootstrap import DEFAULT_CONFIG, get_configuration
from .configuration import ValidationError, UnknownSettingError


def _print_cfg(d: dict, indent: str = ""):
    for k, v in d.items():
        if isinstance(v, dict):
            print(f"{indent}{k}:")
            _print_cfg(v, indent + "  ")
        else:
            print(f"{indent}{k} = {v!r}")


def handle_config(args):
    configuration = get_configuration()

    if args.action is None or args.action == "show":
        _print_cfg(configuration.snapshot())

    elif args.action == "reset":
        _reset_to_defaults(configuration)
        print("Config reset to defaults.")

    elif args.action == "set":
        if not args.key or args.value is None:
            print("Usage: novi config set <key> <value>")
            return
        try:
            parsed = int(args.value)
        except ValueError:
            try:
                parsed = float(args.value)
            except ValueError:
                parsed = args.value
        try:
            configuration.set(args.key, parsed, by="cli")
            print(f"Set {args.key} = {parsed!r}")
        except UnknownSettingError:
            print(f"'{args.key}' is not a registered setting.")
        except ValidationError as e:
            print(f"'{args.key}' failed validation: {e.errors}")


def _reset_to_defaults(configuration):
    """Rewrite the config file to framework defaults (framework-owned write)."""
    configuration.store.write(dict(DEFAULT_CONFIG))
    configuration.initialize()