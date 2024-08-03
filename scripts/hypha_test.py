import asyncio
from hypha_rpc import login, connect_to_server


async def start_server(server_url):
    user_info = await login({"server_url": server_url, "profile": True})
    print(f"Logged in as: {user_info}")
    server = await connect_to_server(
        {"server_url": server_url, "token": user_info.token}
    )
    server.on("connected", lambda info: print("Connected to server: ", info))

    def hello(name):
        print("Hello " + name)
        return "Hello " + name

    await server.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "config": {"visibility": "public"},
            "hello": hello,
        }
    )

    print(f"Hello world service registered at workspace: {server.config.workspace}")
    print(
        f"Test it with the HTTP proxy: {server_url}/{server.config.workspace}/services/hello-world/hello?name=John"
    )


if __name__ == "__main__":
    server_url = "http://localhost:9527"
    loop = asyncio.get_event_loop()
    loop.create_task(start_server(server_url))
    loop.run_forever()
