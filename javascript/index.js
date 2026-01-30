// Export the unified client (supports both websocket and http transport)
const hyphaRpcClient = require("./dist/hypha-rpc-client.js");

// Provide backward compatibility alias
module.exports = {
  hyphaRpcClient,
  hyphaWebsocketClient: hyphaRpcClient // backward compatibility alias
};