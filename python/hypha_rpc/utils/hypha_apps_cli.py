import os
import sys
import json
import base64
import mimetypes
import argparse
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv, find_dotenv
from hypha_rpc import connect_to_server
import yaml

load_dotenv(dotenv_path=find_dotenv(usecwd=True))

async def connect() -> Any:
    server_url = os.getenv("HYPHA_SERVER_URL")
    token = os.getenv("HYPHA_TOKEN")
    workspace = os.getenv("HYPHA_WORKSPACE")
    client_id = os.getenv("HYPHA_CLIENT_ID", "hypha-apps-cli")

    if not all([server_url, token, workspace]):
        print("❌ Missing environment variables. Set HYPHA_SERVER_URL, HYPHA_TOKEN, HYPHA_WORKSPACE", file=sys.stderr)
        sys.exit(1)

    return await connect_to_server({
        "client_id": client_id,
        "server_url": server_url,
        "token": token,
        "workspace": workspace,
    })


def progress_callback(info: Dict[str, Any]):
    emoji = {
        "info": "ℹ️",
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "upload": "📤",
        "download": "📥"
    }.get(info.get("type", ""), "🔸")
    print(f"{emoji} {info.get('message', '')}")

def load_manifest(manifest_path: str) -> Dict[str, Any]:
    with open(manifest_path, "r", encoding="utf-8") as f:
        content = f.read()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return yaml.safe_load(content)

def infer_format_and_content(filepath: Path) -> Dict[str, Any]:
    mime_type, _ = mimetypes.guess_type(filepath)
    if mime_type == "application/json":
        with open(filepath, "r", encoding="utf-8") as f:
            return {
                "name": str(filepath),
                "content": json.load(f),
                "format": "json"
            }
    elif mime_type and mime_type.startswith("text/"):
        with open(filepath, "r", encoding="utf-8") as f:
            return {
                "name": str(filepath),
                "content": f.read(),
                "format": "text"
            }
    else:
        with open(filepath, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
            return {
                "name": str(filepath),
                "content": encoded,
                "format": "base64"
            }

def collect_files(directory: str) -> List[Dict[str, Any]]:
    files = []
    root = Path(directory).resolve()
    for path in root.rglob("*"):
        if path.is_file():
            relative_path = path.relative_to(root)
            file_data = infer_format_and_content(path)
            file_data["name"] = str(relative_path).replace("\\", "/")
            files.append(file_data)
    return files

async def install_app(app_id: str, source_path: str, manifest_path: str, files_path: str, overwrite: bool = False):
    api = await connect()
    controller = await api.get_service("public/server-apps")

    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()
    manifest = load_manifest(manifest_path)
    files = collect_files(files_path) if files_path else []

    print(f"📦 Installing app '{app_id}' from {source_path} with manifest {manifest_path}...")
    await controller.install(
        app_id=app_id,
        source=source,
        manifest=manifest,
        files=files,
        overwrite=overwrite,
        progress_callback=progress_callback
    )
    
    app_info = await controller.get_app_info(app_id)
    print(f"📦 App info: {json.dumps(app_info, indent=2)}")
    print(f"✅ App '{app_id}' successfully installed")

async def start_app(app_id: str):
    api = await connect()
    controller = await api.get_service("public/server-apps")
    print(f"🚀 Starting app '{app_id}'...")
    started = await controller.start(app_id, timeout=30, progress_callback=progress_callback)
    print("✅ Available services:")
    for service in started.services:
        print(f"  - {service.id.split(':')[1]} ({service.get('name', '')}): {service.get('description', 'No description')}")
    print(f"🚀 Started app with client ID: {started.id}")

async def stop_app(app_id: str):
    api = await connect()
    controller = await api.get_service("public/server-apps")
    running = await controller.list_running()
    found = next((a for a in running if a.id == app_id), None)
    if not found:
        print(f"⚠️ App '{app_id}' is not currently running.")
        return
    await controller.stop(app_id)
    print(f"🛑 Stopped app '{app_id}'.")

async def stop_all_apps():
    api = await connect()
    controller = await api.get_service("public/server-apps")
    running = await controller.list_running()
    if not running:
        print("⚠️ No apps are currently running.")
        return
    for app in running:
        await controller.stop(app.id)
        print(f"🛑 Stopped app '{app.id}'.")

async def uninstall_app(app_id: str):
    api = await connect()
    controller = await api.get_service("public/server-apps")
    await controller.uninstall(app_id)
    print(f"🗑️ Uninstalled app '{app_id}'")

async def list_apps(running: bool = False):
    api = await connect()
    controller = await api.get_service("public/server-apps")
    if running:
        apps = await controller.list_running()
        print(f"🟢 Running apps ({len(apps)}):")
    else:
        apps = await controller.list_apps()
        print(f"📦 Installed apps ({len(apps)}):")

    for app in apps:
        print(f"- {app.get('name')} (app_id: `{app.id}`): {app.get('description', 'No description')}")

async def list_services():
    api = await connect()
    services = await api.list_services()
    print(f"🔧 Available services ({len(services)}):")
    for svc in services:
        # use an emjoi for the service name
        print(f"🔧 {svc['id']}")
        print(f"  {json.dumps(svc, indent=2)}")

def main():
    parser = argparse.ArgumentParser(description="Hypha Apps CLI")
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="Install an app")
    install.add_argument("--app-id", required=True)
    install.add_argument("--source", required=True)
    install.add_argument("--manifest", required=True)
    install.add_argument("--files", required=False)
    install.add_argument("--overwrite", action="store_true")

    start = subparsers.add_parser("start", help="Start an app")
    start.add_argument("--app-id", required=True)

    stop = subparsers.add_parser("stop", help="Stop a running app")
    stop.add_argument("--app-id", required=True)
    
    stop_all = subparsers.add_parser("stop-all", help="Stop all running apps")
    stop_all.set_defaults(func=stop_all_apps)

    uninstall = subparsers.add_parser("uninstall", help="Uninstall an app")
    uninstall.add_argument("--app-id", required=True)

    subparsers.add_parser("list-installed", help="List all installed apps")
    subparsers.add_parser("list-running", help="List all currently running apps")
    subparsers.add_parser("list-services", help="List all available services")

    args = parser.parse_args()

    if args.command == "install":
        asyncio.run(install_app(args.app_id, args.source, args.manifest, args.files, args.overwrite))
    elif args.command == "start":
        asyncio.run(start_app(args.app_id))
    elif args.command == "stop":
        asyncio.run(stop_app(args.app_id))
    elif args.command == "stop-all":
        asyncio.run(stop_all_apps())
    elif args.command == "uninstall":
        asyncio.run(uninstall_app(args.app_id))
    elif args.command == "list-installed":
        asyncio.run(list_apps(running=False))
    elif args.command == "list-running":
        asyncio.run(list_apps(running=True))
    elif args.command == "list-services":
        asyncio.run(list_services())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
