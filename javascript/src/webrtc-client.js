import { RPC } from "./rpc.js";
import { assert, randId } from "./utils";
import { schemaFunction } from "./utils/schema.js";

class WebRTCConnection {
  constructor(channel) {
    this._data_channel = channel;
    this._handle_message = null;
    this._reconnection_token = null;
    this._handle_disconnected = null;
    this._handle_connected = () => {};
    this.manager_id = null;
    this._last_message = null;
    this._data_channel.onopen = async () => {
      if (this._last_message) {
        console.info("Resending last message after connection established");
        this._data_channel.send(this._last_message);
        this._last_message = null;
      }
      this._handle_connected &&
        this._handle_connected({ channel: this._data_channel });
    };
    this._data_channel.onmessage = async (event) => {
      let data = event.data;
      if (data instanceof Blob) {
        data = await data.arrayBuffer();
      }
      this._handle_message(data);
    };
    const self = this;
    this._data_channel.onclose = function () {
      if (this._handle_disconnected) this._handle_disconnected("closed");
      console.log("websocket closed");
      self._data_channel = null;
    };
  }

  on_disconnected(handler) {
    this._handle_disconnected = handler;
  }

  on_connected(handler) {
    this._handle_connected = handler;
  }

  on_message(handler) {
    assert(handler, "handler is required");
    this._handle_message = handler;
  }

  async emit_message(data) {
    assert(this._handle_message, "No handler for message");
    try {
      this._last_message = data;
      this._data_channel.send(data);
      this._last_message = null;
    } catch (exp) {
      console.error(`Failed to send data, error: ${exp}`);
      throw exp;
    }
  }

  async disconnect(reason) {
    this._last_message = null;
    this._data_channel = null;
    console.info(`data channel connection disconnected (${reason})`);
  }
}

async function _setupRPC(config) {
  assert(config.channel, "No channel provided");
  assert(config.workspace, "No workspace provided");
  const channel = config.channel;
  const clientId = config.client_id || randId();
  const connection = new WebRTCConnection(channel);
  config.context = config.context || {};
  config.context.connection_type = "webrtc";
  config.context.ws = config.workspace;
  const rpc = new RPC(connection, {
    client_id: clientId,
    default_context: config.context,
    name: config.name,
    method_timeout: config.method_timeout || 10.0,
    workspace: config.workspace,
    app_id: config.app_id,
    long_message_chunk_size: config.long_message_chunk_size,
  });
  return rpc;
}

async function _createOffer(params, server, config, onInit, context) {
  config = config || {};
  let offer = new RTCSessionDescription({
    sdp: params.sdp,
    type: params.type,
  });

  let pc = new RTCPeerConnection({
    iceServers: config.ice_servers || [
      { urls: ["stun:stun.l.google.com:19302"] },
    ],
    sdpSemantics: "unified-plan",
  });

  if (server) {
    pc.addEventListener("datachannel", async (event) => {
      const channel = event.channel;
      let ctx = null;
      if (context && context.user) ctx = { user: context.user, ws: context.ws };
      const rpc = await _setupRPC({
        channel: channel,
        client_id: channel.label,
        workspace: server.config.workspace,
        context: ctx,
      });
      // Map all the local services to the webrtc client
      rpc._services = server.rpc._services;
    });
  }

  if (onInit) {
    await onInit(pc);
  }

  await pc.setRemoteDescription(offer);

  let answer = await pc.createAnswer();
  await pc.setLocalDescription(answer);

  return {
    sdp: pc.localDescription.sdp,
    type: pc.localDescription.type,
    workspace: server.config.workspace,
  };
}

async function getRTCService(server, service_id, config) {
  config = config || {};
  config.peer_id = config.peer_id || randId();

  const pc = new RTCPeerConnection({
    iceServers: config.ice_servers || [
      { urls: ["stun:stun.l.google.com:19302"] },
    ],
    sdpSemantics: "unified-plan",
  });

  return new Promise(async (resolve, reject) => {
    try {
      pc.addEventListener(
        "connectionstatechange",
        () => {
          if (pc.connectionState === "failed") {
            pc.close();
            reject(new Error("WebRTC Connection failed"));
          } else if (pc.connectionState === "closed") {
            reject(new Error("WebRTC Connection closed"));
          } else {
            console.log("WebRTC Connection state: ", pc.connectionState);
          }
        },
        false,
      );

      if (config.on_init) {
        await config.on_init(pc);
        delete config.on_init;
      }
      let channel = pc.createDataChannel(config.peer_id, { ordered: true });
      channel.binaryType = "arraybuffer";
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const svc = await server.getService(service_id);
      const answer = await svc.offer({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
      });

      channel.onopen = () => {
        config.channel = channel;
        config.workspace = answer.workspace;
        // Wait for the channel to be open before returning the rpc
        // This is needed for safari to work
        setTimeout(async () => {
          const rpc = await _setupRPC(config);
          pc.rpc = rpc;
          async function get_service(name, ...args) {
            assert(
              !name.includes(":"),
              "WebRTC service name should not contain ':'",
            );
            assert(
              !name.includes("/"),
              "WebRTC service name should not contain '/'",
            );
            return await rpc.get_remote_service(
              config.workspace + "/" + config.peer_id + ":" + name,
              ...args,
            );
          }
          async function disconnect() {
            await rpc.disconnect();
            pc.close();
          }
          pc.getService = schemaFunction(get_service, {
            name: "getService",
            description: "Get a remote service via webrtc",
            parameters: {
              type: "object",
              properties: {
                service_id: {
                  type: "string",
                  description:
                    "Service ID. This should be a service id in the format: 'workspace/service_id', 'workspace/client_id:service_id' or 'workspace/client_id:service_id@app_id'",
                },
                config: {
                  type: "object",
                  description: "Options for the service",
                },
              },
              required: ["id"],
            },
          });
          pc.disconnect = schemaFunction(disconnect, {
            name: "disconnect",
            description: "Disconnect from the webrtc connection via webrtc",
            parameters: { type: "object", properties: {} },
          });
          pc.registerCodec = schemaFunction(rpc.register_codec, {
            name: "registerCodec",
            description: "Register a codec for the webrtc connection",
            parameters: {
              type: "object",
              properties: {
                codec: {
                  type: "object",
                  description: "Codec to register",
                  properties: {
                    name: { type: "string" },
                    type: {},
                    encoder: { type: "function" },
                    decoder: { type: "function" },
                  },
                },
              },
            },
          });
          resolve(pc);
        }, 500);
      };

      channel.onclose = () => reject(new Error("Data channel closed"));

      await pc.setRemoteDescription(
        new RTCSessionDescription({
          sdp: answer.sdp,
          type: answer.type,
        }),
      );
    } catch (e) {
      reject(e);
    }
  });
}

async function registerRTCService(server, service_id, config) {
  config = config || {
    visibility: "protected",
    require_context: true,
  };
  const onInit = config.on_init;
  delete config.on_init;
  return await server.registerService({
    id: service_id,
    config,
    offer: (params, context) =>
      _createOffer(params, server, config, onInit, context),
  });
}

export { getRTCService, registerRTCService };
