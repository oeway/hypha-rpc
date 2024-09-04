import os
import httpx
from simpervisor import SupervisedProcess


async def _http_ready_func(url, p, logger=None):
    """Check if the http service is ready."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(url)
            # We only care if we get back *any* response, not just 200
            # If there's an error response, that can be shown directly to the user
            if logger:
                logger.debug(f"Got code {resp.status} back from {url}")
            return True
        except httpx.RequestError as exc:
            if logger:
                logger.debug(f"Connection to {url} failed: {exc}")
            return False


async def launch_external_services(
    server: any,
    command: str,
    name=None,
    env=None,
    check_services=None,
    check_url=None,
    timeout=5,
    logger=None,
):
    """
    Launch external services asynchronously and monitors their status (requires simpervisor).

    Args:
        server: The server instance for which the services are to be launched.
        command (str): The command to be executed to start the service. Any placeholders such as {server_url},
                       {workspace}, and {token} in the command will be replaced with actual values.
        name (str, optional): The name of the service. If not provided, the first argument of the command is used as the name.
        env (dict, optional): A dictionary of environment variables to be set for the service.
        check_services (list, optional): A list of service IDs to be checked for readiness. The service is considered ready
                                          if all services in the list are available.
        check_url (str, optional): A URL to be checked for readiness. The service is considered ready if the URL is accessible.
        timeout (int, optional): The maximum number of seconds to wait for the service to be ready. Defaults to 5.
        logger (logging.Logger, optional): A logger instance to be used for logging messages. If not provided, no messages
                                           are logged.

    Raises:
        Exception: If the service fails to start or does not become ready within the specified timeout.

    Returns:
        proc (SupervisedProcess): The process object for the service. You can call proc.kill() to kill the process.
    """

    token = await server.generate_token()
    # format the cmd so we fill in the {server_url} placeholder
    command = command.format(
        server_url=server.config.local_base_url,
        workspace=server.config["workspace"],
        token=token,
    )
    command = [c.strip() for c in command.split() if c.strip()]
    assert len(command) > 0, f"Invalid command: {command}"
    name = name or command[0]
    # split command into list, strip off spaces and filter out empty strings
    server_env = os.environ.copy()
    server_env.update(env or {})

    async def ready_function(p):
        if check_services:
            for service_id in check_services:
                try:
                    await server.get_service(
                        server.config["workspace"] + "/" + service_id
                    )
                except Exception:
                    return False
            return True
        if check_url:
            return await _http_ready_func(check_url, p, logger=logger)
        return True

    proc = SupervisedProcess(
        name,
        *command,
        env=server_env,
        always_restart=False,
        ready_func=ready_function,
        ready_timeout=timeout,
        log=logger,
    )

    try:
        await proc.start()

        is_ready = await proc.ready()

        if not is_ready:
            await proc.kill()
            raise Exception(f"External services ({name}) failed to start")
    except:
        if logger:
            logger.exception(f"External services ({name}) failed to start")
        raise
    return proc
