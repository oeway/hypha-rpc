import argparse
import importlib
import asyncio
from hypha_rpc import connect_to_server, login
from fastapi import FastAPI


async def serve_app(
    app: FastAPI,
    server_url: str,
    service_id: str,
    workspace: str = None,
    token: str = None,
    disable_ssl: bool = False,
    service_name: str = None,
):
    # Connection options
    connection_options = {
        "server_url": server_url,
        "workspace": workspace,
        "token": token,
    }
    if disable_ssl:
        connection_options["ssl"] = False

    # Connect to the Hypha server
    server = await connect_to_server(connection_options)

    async def serve_fastapi(args, context=None):
        await app(args["scope"], args["receive"], args["send"])

    svc_info = await server.register_service(
        {
            "id": service_id,
            "name": service_name or service_id,
            "type": "ASGI",
            "serve": serve_fastapi,
            "config": {"visibility": "public"},
        }
    )

    print(
        f"Access your app at: {server_url}/{server.config.workspace}/apps/{svc_info['id'].split(':')[1]}"
    )

    # Keep the server running
    await server.serve()


async def main(args):
    if args.login:
        if args.token:
            raise ValueError("Cannot use --token when --login is enabled.")
        login_options = {"server_url": args.server_url}
        if args.disable_ssl:
            login_options["ssl"] = False
        # Perform login to get the token
        token = await login(login_options)
    else:
        if not args.token:
            raise ValueError("Either --token or --login must be provided.")
        token = args.token

    # Import the app dynamically
    module_name, app_name = args.app.split(":")
    module = importlib.import_module(module_name)
    app = getattr(module, app_name)

    if not isinstance(app, FastAPI):
        raise TypeError("The specified app is not a FastAPI instance")

    # Start serving the app asynchronously
    await serve_app(
        app,
        args.server_url,
        args.id,
        args.workspace,
        token,
        args.disable_ssl,
        args.name,
    )


def main_entry():
    parser = argparse.ArgumentParser(description="Serve FastAPI app using Hypha")
    parser.add_argument(
        "app", type=str, help="The FastAPI app to serve (e.g., myapp:app)"
    )
    parser.add_argument("--id", type=str, required=True, help="The service ID")
    parser.add_argument(
        "--name", type=str, default=None, required=False, help="The service name"
    )
    parser.add_argument(
        "--server-url", type=str, required=True, help="The Hypha server URL"
    )
    parser.add_argument(
        "--workspace", type=str, required=True, help="The workspace to connect to"
    )
    parser.add_argument(
        "--token",
        type=str,
        help="The token for authentication (not needed if --login is used)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Enable login to get the token (overrides --token)",
    )
    parser.add_argument(
        "--disable-ssl", action="store_true", help="Disable SSL verification"
    )

    args = parser.parse_args()

    # Run the main coroutine indefinitely
    asyncio.run(main(args))


if __name__ == "__main__":
    main_entry()
