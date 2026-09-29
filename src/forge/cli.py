import argparse
import asyncio
from collections.abc import Sequence

from forge.config import get_settings
from forge.resources import RuntimeResources
from forge.security.api_keys import SUPPORTED_SCOPES, issue_api_key


async def create_key(name: str, scopes: set[str]) -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    try:
        async with resources.sessions.begin() as session:
            issued = await issue_api_key(
                session,
                name=name,
                scopes=scopes,
                pepper=settings.api_key_pepper.get_secret_value(),
            )
    finally:
        await resources.close()

    print("API key created. Copy it now; Forge does not store the secret:")
    print(issued.credential)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge")
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("key", help="manage API keys")
    key_commands = key.add_subparsers(dest="key_command", required=True)
    create = key_commands.add_parser("create", help="create an API key")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--scope",
        action="append",
        dest="scopes",
        choices=sorted(SUPPORTED_SCOPES),
        required=True,
        help="repeat to grant more than one scope",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "key" and args.key_command == "create":
        asyncio.run(create_key(args.name, set(args.scopes)))
