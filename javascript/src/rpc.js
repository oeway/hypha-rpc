/**
 * Contains the RPC object used both by the application
 * site, and by each plugin
 */
import {
  randId,
  typedArrayToDtype,
  dtypeToTypedArray,
  MessageEmitter,
  assert,
  waitFor,
  convertCase,
  expandKwargs,
  Semaphore,
  isAsyncGenerator,
  isGenerator,
} from "./utils/index.js";
import { schemaFunction } from "./utils/schema.js";

import { encode as msgpack_packb, decodeMulti } from "@msgpack/msgpack";

export const API_VERSION = 3;
const CHUNK_SIZE = 1024 * 256;
const CONCURRENCY_LIMIT = 30;

const ArrayBufferView = Object.getPrototypeOf(
  Object.getPrototypeOf(new Uint8Array()),
).constructor;

function _appendBuffer(buffer1, buffer2) {
  const tmp = new Uint8Array(buffer1.byteLength + buffer2.byteLength);
  tmp.set(new Uint8Array(buffer1), 0);
  tmp.set(new Uint8Array(buffer2), buffer1.byteLength);
  return tmp.buffer;
}

function indexObject(obj, is) {
  if (!is) throw new Error("undefined index");
  if (typeof is === "string") return indexObject(obj, is.split("."));
  else if (is.length === 0) return obj;
  else return indexObject(obj[is[0]], is.slice(1));
}

// Simple fallback schema generation - no docstring parsing for JS

function _get_schema(obj, name = null, skipContext = false) {
  if (Array.isArray(obj)) {
    return obj.map((v, i) => _get_schema(v, null, skipContext));
  } else if (typeof obj === "object" && obj !== null) {
    let schema = {};
    for (let k in obj) {
      schema[k] = _get_schema(obj[k], k, skipContext);
    }
    return schema;
  } else if (typeof obj === "function") {
    if (obj.__schema__) {
      const schema = JSON.parse(JSON.stringify(obj.__schema__));
      if (name) {
        schema.name = name;
        obj.__schema__.name = name;
      }
      if (skipContext) {
        if (schema.parameters && schema.parameters.properties) {
          delete schema.parameters.properties["context"];
        }
        if (schema.parameters && schema.parameters.required) {
          const contextIndex = schema.parameters.required.indexOf("context");
          if (contextIndex > -1) {
            schema.parameters.required.splice(contextIndex, 1);
          }
        }
      }
      return { type: "function", function: schema };
    } else {
      // Simple fallback for JavaScript - just return basic function schema with name
      const funcName = name || obj.name || "function";
      return {
        type: "function",
        function: {
          name: funcName,
        },
      };
    }
  } else if (typeof obj === "number") {
    return { type: "number" };
  } else if (typeof obj === "string") {
    return { type: "string" };
  } else if (typeof obj === "boolean") {
    return { type: "boolean" };
  } else if (obj === null) {
    return { type: "null" };
  } else {
    return {};
  }
}

function _annotate_service(service, serviceTypeInfo) {
  function validateKeys(serviceDict, schemaDict, path = "root") {
    // Validate that all keys in schemaDict exist in serviceDict
    for (let key in schemaDict) {
      if (!serviceDict.hasOwnProperty(key)) {
        throw new Error(`Missing key '${key}' in service at path '${path}'`);
      }
    }

    // Check for any unexpected keys in serviceDict
    for (let key in serviceDict) {
      if (key !== "type" && !schemaDict.hasOwnProperty(key)) {
        throw new Error(`Unexpected key '${key}' in service at path '${path}'`);
      }
    }
  }

  function annotateRecursive(newService, schemaInfo, path = "root") {
    if (typeof newService === "object" && !Array.isArray(newService)) {
      validateKeys(newService, schemaInfo, path);
      for (let k in newService) {
        let v = newService[k];
        let newPath = `${path}.${k}`;
        if (typeof v === "object" && !Array.isArray(v)) {
          annotateRecursive(v, schemaInfo[k], newPath);
        } else if (typeof v === "function") {
          if (schemaInfo.hasOwnProperty(k)) {
            newService[k] = schemaFunction(v, {
              name: schemaInfo[k]["name"],
              description: schemaInfo[k].description || "",
              parameters: schemaInfo[k]["parameters"],
            });
          } else {
            throw new Error(
              `Missing schema for function '${k}' at path '${newPath}'`,
            );
          }
        }
      }
    } else if (Array.isArray(newService)) {
      if (newService.length !== schemaInfo.length) {
        throw new Error(`Length mismatch at path '${path}'`);
      }
      newService.forEach((v, i) => {
        let newPath = `${path}[${i}]`;
        if (typeof v === "object" && !Array.isArray(v)) {
          annotateRecursive(v, schemaInfo[i], newPath);
        } else if (typeof v === "function") {
          if (schemaInfo.hasOwnProperty(i)) {
            newService[i] = schemaFunction(v, {
              name: schemaInfo[i]["name"],
              description: schemaInfo[i].description || "",
              parameters: schemaInfo[i]["parameters"],
            });
          } else {
            throw new Error(
              `Missing schema for function at index ${i} in path '${newPath}'`,
            );
          }
        }
      });
    }
  }

  validateKeys(service, serviceTypeInfo["definition"]);
  annotateRecursive(service, serviceTypeInfo["definition"]);
  return service;
}

function getFunctionInfo(func) {
  const funcString = func.toString();

  // Extract function name
  const nameMatch = funcString.match(/function\s*(\w*)/);
  const name = (nameMatch && nameMatch[1]) || "";

  // Extract function parameters, excluding comments
  const paramsMatch = funcString.match(/\(([^)]*)\)/);
  let params = "";
  if (paramsMatch) {
    params = paramsMatch[1]
      .split(",")
      .map((p) =>
        p
          .replace(/\/\*.*?\*\//g, "") // Remove block comments
          .replace(/\/\/.*$/g, ""),
      ) // Remove line comments
      .filter((p) => p.trim().length > 0) // Remove empty strings after removing comments
      .map((p) => p.trim()) // Trim remaining whitespace
      .join(", ");
  }

  // Extract function docstring (block comment)
  let docMatch = funcString.match(/\)\s*\{\s*\/\*([\s\S]*?)\*\//);
  const docstringBlock = (docMatch && docMatch[1].trim()) || "";

  // Extract function docstring (line comment)
  docMatch = funcString.match(/\)\s*\{\s*(\/\/[\s\S]*?)\n\s*[^\s\/]/);
  const docstringLine =
    (docMatch &&
      docMatch[1]
        .split("\n")
        .map((s) => s.replace(/^\/\/\s*/, "").trim())
        .join("\n")) ||
    "";

  const docstring = docstringBlock || docstringLine;
  return (
    name &&
    params.length > 0 && {
      name: name,
      sig: params,
      doc: docstring,
    }
  );
}

function concatArrayBuffers(buffers) {
  var buffersLengths = buffers.map(function (b) {
      return b.byteLength;
    }),
    totalBufferlength = buffersLengths.reduce(function (p, c) {
      return p + c;
    }, 0),
    unit8Arr = new Uint8Array(totalBufferlength);
  buffersLengths.reduce(function (p, c, i) {
    unit8Arr.set(new Uint8Array(buffers[i]), p);
    return p + c;
  }, 0);
  return unit8Arr.buffer;
}

class Timer {
  constructor(timeout, callback, args, label) {
    this._timeout = timeout;
    this._callback = callback;
    this._args = args;
    this._label = label || "timer";
    this._task = null;
    this.started = false;
  }

  start() {
    if (this.started) {
      this.reset();
    } else {
      this._task = setTimeout(() => {
        this._callback.apply(this, this._args);
      }, this._timeout * 1000);
      this.started = true;
    }
  }

  clear() {
    if (this._task && this.started) {
      clearTimeout(this._task);
      this._task = null;
      this.started = false;
    } else {
      console.warn(`Clearing a timer (${this._label}) which is not started`);
    }
  }

  reset() {
    if (this._task) {
      clearTimeout(this._task);
    }
    this._task = setTimeout(() => {
      this._callback.apply(this, this._args);
    }, this._timeout * 1000);
    this.started = true;
  }
}

class RemoteService extends Object {}

/**
 * RPC object represents a single site in the
 * communication protocol between the application and the plugin
 *
 * @param {Object} connection a special object allowing to send
 * and receive messages from the opposite site (basically it
 * should only provide send() and onMessage() methods)
 */
export class RPC extends MessageEmitter {
  constructor(
    connection,
    {
      client_id = null,
      default_context = null,
      name = null,
      codecs = null,
      method_timeout = null,
      max_message_buffer_size = 0,
      debug = false,
      workspace = null,
      silent = false,
      app_id = null,
      server_base_url = null,
      long_message_chunk_size = null,
      enable_http_transmission = true,
      http_transmission_threshold = 1024 * 1024, // 1MB default
      multipart_threshold = 10 * 1024 * 1024, // 10MB default
      multipart_size = 6 * 1024 * 1024, // 6MB default
      max_parallel_uploads = 5, // 5 parallel uploads default
    },
  ) {
    super(debug);
    this._codecs = codecs || {};
    assert(client_id && typeof client_id === "string");
    assert(client_id, "client_id is required");
    this._client_id = client_id;
    this._name = name;
    this._app_id = app_id || "*";
    this._local_workspace = workspace;
    this._silent = silent;
    this.default_context = default_context || {};
    this._method_annotations = new WeakMap();
    this._max_message_buffer_size = max_message_buffer_size;
    this._chunk_store = {};
    this._method_timeout = method_timeout || 30;
    this._server_base_url = server_base_url;
    this._long_message_chunk_size = long_message_chunk_size || CHUNK_SIZE;

    // Initialize HTTP message transmission configuration
    this._enable_http_transmission = enable_http_transmission;
    this._http_transmission_threshold = http_transmission_threshold;
    this._multipart_threshold = multipart_threshold;
    // Ensure multipart_size is at least 5MB (S3 minimum requirement)
    this._multipart_size = Math.max(multipart_size, 5 * 1024 * 1024);
    this._max_parallel_uploads = max_parallel_uploads;
    this._http_message_transmission_available = false;
    this._s3controller = null;

    // make sure there is an execute function
    this._services = {};
    this._object_store = {
      services: this._services,
    };

    // Set up global unhandled promise rejection handler for RPC-related errors
    const handleUnhandledRejection = (event) => {
      const reason = event.reason;
      if (reason && typeof reason === "object") {
        // Check if this is a "Method not found" or "Session not found" error that we can ignore
        const reasonStr = reason.toString();
        if (
          reasonStr.includes("Method not found") ||
          reasonStr.includes("Session not found") ||
          reasonStr.includes("Method expired") ||
          reasonStr.includes("Session not found")
        ) {
          console.debug(
            "Ignoring expected method/session not found error:",
            reason,
          );
          event.preventDefault(); // Prevent the default unhandled rejection behavior
          return;
        }
      }
      console.warn("Unhandled RPC promise rejection:", reason);
    };

    // Only set the handler if we haven't already set one for this RPC instance
    if (typeof window !== "undefined" && !window._hypha_rejection_handler_set) {
      window.addEventListener("unhandledrejection", handleUnhandledRejection);
      window._hypha_rejection_handler_set = true;
    } else if (
      typeof process !== "undefined" &&
      !process._hypha_rejection_handler_set
    ) {
      process.on("unhandledRejection", (reason, promise) => {
        handleUnhandledRejection({ reason, promise, preventDefault: () => {} });
      });
      process._hypha_rejection_handler_set = true;
    }

    if (connection) {
      this.add_service({
        id: "built-in",
        type: "built-in",
        name: `Built-in services for ${this._local_workspace}/${this._client_id}`,
        config: {
          require_context: true,
          visibility: "public",
          api_version: API_VERSION,
        },
        ping: this._ping.bind(this),
        get_service: this.get_local_service.bind(this),
        message_cache: {
          create: this._create_message.bind(this),
          append: this._append_message.bind(this),
          set: this._set_message.bind(this),
          process: this._process_message.bind(this),
          remove: this._remove_message.bind(this),
        },
      });
      this.on("method", this._handle_method.bind(this));
      this.on("http-message", this._handle_http_message.bind(this));
      this.on("error", console.error);

      assert(connection.emit_message && connection.on_message);
      assert(
        connection.manager_id !== undefined,
        "Connection must have manager_id",
      );
      this._emit_message = connection.emit_message.bind(connection);
      connection.on_message(this._on_message.bind(this));
      this._connection = connection;
      const onConnected = async (connectionInfo) => {
        // Extract manager_id from connectionInfo if available
        if (!this._silent) {
          // Only try to register services if we have connection info or manager_id
          if (connectionInfo) {
            console.info(
              `Connection info received in onConnected:`,
              connectionInfo,
            );
            // Update the connection's manager_id from connectionInfo if available
            if (connectionInfo.manager_id && !this._connection.manager_id) {
              this._connection.manager_id = connectionInfo.manager_id;
            }
          }

          // Manager ID must always be available after connection
          assert(
            this._connection.manager_id,
            `Manager ID must be available after connection, connection_info: ${JSON.stringify(connectionInfo)}`,
          );
          console.debug(`Manager ID available: ${this._connection.manager_id}`);

          // Always register services since manager_id is guaranteed to be present
          console.info("Connection established, reporting services...");
          try {
            // Get manager service with snake_case for method names
            const manager = await this.get_manager_service({ case_conversion: "snake" });
            const services = Object.values(this._services);
            const servicesCount = services.length;
            let registeredCount = 0;
            const failedServices = [];

            for (let service of services) {
              try {
                const serviceInfo = this._extract_service_info(service);
                await manager.register_service(serviceInfo);
                registeredCount++;
                console.debug(
                  `Successfully registered service: ${service.id || "unknown"}`,
                );
              } catch (serviceError) {
                failedServices.push(service.id || "unknown");
                console.error(
                  `Failed to register service ${service.id || "unknown"}: ${serviceError}`,
                );
              }
            }

            if (registeredCount === servicesCount) {
              console.info(
                `Successfully registered all ${registeredCount} services with the server`,
              );
            } else {
              console.warn(
                `Only registered ${registeredCount} out of ${servicesCount} services with the server. Failed services: ${failedServices.join(", ")}`,
              );
            }

            // Fire event with registration status
            this._fire("services_registered", {
              total: servicesCount,
              registered: registeredCount,
              failed: failedServices,
            });

            // Initialize HTTP message transmission AFTER services are registered
            // This ensures the manager service is fully available
            if (this._enable_http_transmission) {
              this._http_message_transmission_available = false;
              this._s3controller = null;
              try {
                await this._initialize_http_message_transmission();
              } catch (error) {
                console.debug(
                  "Failed to initialize HTTP message transmission:",
                  error.message,
                );
              }
            }
          } catch (managerError) {
            console.error(
              `Failed to get manager service for registering services: ${managerError}`,
            );
            // Fire event with error status
            this._fire("services_registration_failed", {
              error: managerError.toString(),
              total_services: Object.keys(this._services).length,
            });
          }
        }
        console.info("Connection established:", connectionInfo);

        if (connectionInfo) {
          if (connectionInfo.public_base_url) {
            this._server_base_url = connectionInfo.public_base_url;
          }
          
          // Update the local workspace to match the connection workspace (important for reconnection)
          if (connectionInfo.workspace) {
            const oldWorkspace = this._local_workspace;
            this._local_workspace = connectionInfo.workspace;
            console.warn(`[RECONNECTION DEBUG] Updated local workspace from ${oldWorkspace} to ${this._local_workspace}`);
          }
          
          this._fire("connected", connectionInfo);
        }
      };
      connection.on_connected(onConnected);
      onConnected(connection.connection_info);
    } else {
      this._emit_message = function () {
        console.log("No connection to emit message");
      };
    }
  }

  register_codec(config) {
    if (!config["name"] || (!config["encoder"] && !config["decoder"])) {
      throw new Error(
        "Invalid codec format, please make sure you provide a name, type, encoder and decoder.",
      );
    } else {
      if (config.type) {
        for (let k of Object.keys(this._codecs)) {
          if (this._codecs[k].type === config.type || k === config.name) {
            delete this._codecs[k];
            console.warn("Remove duplicated codec: " + k);
          }
        }
      }
      this._codecs[config["name"]] = config;
    }
  }

  async _ping(msg, context) {
    assert(msg == "ping");
    return "pong";
  }

  async ping(client_id, timeout) {
    let method = this._generate_remote_method({
      _rserver: this._server_base_url,
      _rtarget: client_id,
      _rmethod: "services.built-in.ping",
      _rpromise: true,
      _rdoc: "Ping a remote client",
    });
    assert((await method("ping", timeout)) == "pong");
  }

  _create_message(key, heartbeat, overwrite, context) {
    if (heartbeat) {
      if (this._object_store[key]) {
        this._object_store[key]["timer"].reset();
      } else {
        throw new Error(`session does not exist anymore: ${key}`);
      }
    }

    if (!this._object_store["message_cache"]) {
      this._object_store["message_cache"] = {};
    }
    if (!overwrite && this._object_store["message_cache"][key]) {
      throw new Error(
        `Message with the same key (${key}) already exists in the cache store, please use overwrite=true or remove it first.`,
      );
    }
    this._object_store["message_cache"][key] = {};
  }

  _append_message(key, data, heartbeat, context) {
    if (heartbeat) {
      if (this._object_store[key]) {
        this._object_store[key]["timer"].reset();
      } else {
        throw new Error(`session does not exist anymore: ${key}`);
      }
    }
    const cache = this._object_store["message_cache"];
    if (!cache[key]) {
      throw new Error(`Message with key ${key} does not exists.`);
    }
    assert(data instanceof ArrayBufferView);
    const index = Object.keys(cache[key]).length;
    cache[key][index] = data;
  }

  _set_message(key, index, data, heartbeat, context) {
    if (heartbeat) {
      if (this._object_store[key]) {
        this._object_store[key]["timer"].reset();
      } else {
        throw new Error(`session does not exist anymore: ${key}`);
      }
    }
    const cache = this._object_store["message_cache"];
    if (!cache[key]) {
      throw new Error(`Message with key ${key} does not exists.`);
    }
    assert(data instanceof ArrayBufferView);
    cache[key][index] = data;
  }

  _remove_message(key, context) {
    const cache = this._object_store["message_cache"];
    if (!cache[key]) {
      throw new Error(`Message with key ${key} does not exists.`);
    }
    delete cache[key];
  }

  _process_message(key, heartbeat, context) {
    if (heartbeat) {
      if (this._object_store[key]) {
        this._object_store[key]["timer"].reset();
      } else {
        throw new Error(`session does not exist anymore: ${key}`);
      }
    }
    const cache = this._object_store["message_cache"];
    assert(!!context, "Context is required");
    if (!cache[key]) {
      throw new Error(`Message with key ${key} does not exists.`);
    }
    // Convert dictionary to sorted array before concatenating
    const sortedIndices = Object.keys(cache[key])
      .map(Number)
      .sort((a, b) => a - b);
    const buffers = sortedIndices.map((index) => cache[key][index]);
    cache[key] = concatArrayBuffers(buffers);
    // console.debug(`Processing message ${key} (bytes=${cache[key].byteLength})`);
    let unpacker = decodeMulti(cache[key]);
    const { done, value } = unpacker.next();
    const main = value;
    // Restore essential fields and add trusted context
    Object.assign(main, {
      from: context.from,
      to: context.to,
      ws: context.ws,
    });

    // Preserve authenticated user from the message if available
    if ("user" in context) {
      main.user = context.user;
    }

    // Add trusted context to the reconstructed message
    this._add_context_to_message(main);
    if (!done) {
      let extra = unpacker.next();
      Object.assign(main, extra.value);
    }
    this._fire(main["type"], main);
    // console.debug(
    //   this._client_id,
    //   `Processed message ${key} (bytes=${cache[key].byteLength})`,
    // );
    delete cache[key];
  }

  /**
   * Initialize HTTP message transmission when the S3 service is ready.
   * This method waits for the specific S3 service to be registered, not just the manager.
   */
  async _initialize_http_message_transmission_when_ready() {
    // Wait for S3 service to be available with longer timeouts for CI
    let attempts = 0;
    const maxAttempts = 30; // Increased for CI environments

    console.log("🔄 Waiting for S3 service to be available...");

    while (attempts < maxAttempts) {
      try {
        // Check if manager is ready and connection is stable
        if (this._manager_id && this._connection && this._connection._ready) {
          // Try to get the manager and check if S3 service exists
          const manager = await this.get_manager_service({
            timeout: 5,
            case_conversion: "camel",
          });

          // Check if the S3 service is registered
          try {
            await manager.getService("public/s3-storage", {
              case_conversion: "camel",
              timeout: 3,
            });

            // If we get here, the service exists - now do full initialization
            console.log(
              "✅ S3 service found, initializing HTTP message transmission...",
            );
            await this._initialize_http_message_transmission();
            return; // Success!
          } catch (serviceError) {
            console.log(
              `🔄 S3 service not ready yet (attempt ${attempts + 1}/${maxAttempts}): ${serviceError.message}`,
            );
          }
        } else {
          console.log(
            `🔄 Manager not ready yet (attempt ${attempts + 1}/${maxAttempts})`,
          );
        }
      } catch (error) {
        console.warn(
          `S3 initialization attempt ${attempts + 1}/${maxAttempts} failed:`,
          error.message,
        );
      }

      attempts++;
      if (attempts >= maxAttempts) {
        console.warn(
          "⚠️ Failed to initialize HTTP message transmission: S3 service not available after maximum attempts",
        );
        return;
      }

      // Longer delays for CI environments: start with 2s, max 8s
      const delay = Math.min(2000 * Math.pow(1.2, attempts), 8000);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  /**
   * Add trusted context to a message if it doesn't already have it.
   * This creates clean context with only essential fields (not all message fields).
   * Authentication information should ONLY come from the server.
   */
  _add_context_to_message(main) {
    if (!main.ctx) {
      // Create clean context with only essential fields (not all message fields)
      main.ctx = {};
      Object.assign(main.ctx, this.default_context);

      // Add essential context fields that services expect
      const essential_fields = ["ws", "from", "to"];
      for (const field of essential_fields) {
        if (field in main) {
          main.ctx[field] = main[field];
        }
      }

      // Preserve authenticated user context from the message (server-side authenticated)
      if ("user" in main) {
        main.ctx.user = main.user;
      }
    }
    return main;
  }

  _on_message(message) {
    if (typeof message === "string") {
      const main = JSON.parse(message);
      // Add trusted context to the method call
      this._add_context_to_message(main);
      this._fire(main["type"], main);
    } else if (message instanceof ArrayBuffer) {
      let unpacker = decodeMulti(message);
      const { done, value } = unpacker.next();
      const main = value;
      // Add trusted context to the method call
      this._add_context_to_message(main);
      if (!done) {
        let extra = unpacker.next();
        Object.assign(main, extra.value);
      }
      this._fire(main["type"], main);
    } else if (typeof message === "object") {
      // Add trusted context to the method call
      this._add_context_to_message(message);
      this._fire(message["type"], message);
    } else {
      throw new Error("Invalid message format");
    }
  }

  async _initialize_http_message_transmission() {
    try {
      console.log("🔄 Getting manager service for S3 initialization...");

      // Get the manager service to access S3 controller
      const manager = await this.get_manager_service({
        timeout: 15,
        case_conversion: "camel",
      });

      console.log("✅ Manager service obtained, getting S3 storage service...");

      // Try to get the S3 storage service with retry for CI environments
      let s3Service = null;
      const maxAttempts = 20; // Extended for CI environments
      let attempts = 0;

      while (attempts < maxAttempts && !s3Service) {
        try {
          s3Service = await manager.getService("public/s3-storage", {
            case_conversion: "camel",
            timeout: 10,
          });
          console.log("✅ S3 storage service obtained successfully");
          break; // Success, exit retry loop
        } catch (error) {
          attempts++;
          if (attempts >= maxAttempts) {
            console.warn(
              `❌ S3 service not available after ${maxAttempts} attempts: ${error.message}`,
            );
            console.warn("This could be because:");
            console.warn("1. MinIO server is not running");
            console.warn(
              "2. S3 service is not registered (check --enable-s3 flag)",
            );
            console.warn("3. Server startup timing issue in CI environment");
            return; // Don't throw error, just exit gracefully
          }
          console.warn(
            `🔄 S3 service not ready (attempt ${attempts}/${maxAttempts}), retrying in 1s...`,
          );
          await new Promise((resolve) => setTimeout(resolve, 1000)); // 1 second delay between attempts
        }
      }

      this._s3controller = s3Service;

      console.log("🔄 Checking S3 service methods...");

      // Check if required methods are available and callable
      const requiredMethods = [
        "putFile",
        "getFile",
        "putFileStartMultipart",
        "putFileCompleteMultipart",
      ];
      let allMethodsAvailable = true;

      for (let methodName of requiredMethods) {
        if (
          !this._s3controller[methodName] ||
          typeof this._s3controller[methodName] !== "function"
        ) {
          allMethodsAvailable = false;
          console.warn(`❌ S3 method ${methodName} not available`);
          break;
        } else {
          console.log(`✅ S3 method ${methodName} available`);
        }
      }

      if (allMethodsAvailable) {
        this._http_message_transmission_available = true;
        console.log("🎉 HTTP message transmission initialized successfully!");
        console.log(
          `📊 Thresholds: HTTP=${this.http_message_threshold}MB, Multipart=${this.http_multipart_threshold}MB`,
        );
      } else {
        console.warn(
          "⚠️ HTTP message transmission not available - missing required S3 methods",
        );
        this._http_message_transmission_available = false;
      }
    } catch (error) {
      console.warn(
        "❌ Failed to initialize HTTP message transmission:",
        error.message,
      );
      console.warn("Stack trace:", error.stack);
      this._http_message_transmission_available = false;
    }
  }

  async _send_chunks_http(data, target_id, session_id = null) {
    if (!this._http_message_transmission_available) {
      throw new Error("HTTP message transmission not available");
    }

    // Generate unique file path for the message
    const message_id = session_id || this._generate_uuid();
    const file_path = `tmp/${message_id}`;

    // Determine content length
    const content_length = data.byteLength;
    const start_time = Date.now();

    // Track actual transmission behavior (matching Python structure exactly)
    const transmission_stats = {
      content_length: content_length,
      target_id: target_id,
      session_id: session_id,
      file_path: file_path,
      used_multipart: false,
      transmission_method: "single_upload",
      part_count: 1,
      part_size: content_length,
      multipart_threshold: this._multipart_threshold,
      multipart_size: this._multipart_size,
    };

    // Choose transmission method based on size
    // < multipart_threshold: use simple put_file
    // >= multipart_threshold: use multipart upload
    if (content_length < this._multipart_threshold) {
      await this._transmit_small_message_http(
        file_path,
        data,
        content_length,
        target_id,
      );
    } else {
      // Update stats for multipart transmission
      transmission_stats.used_multipart = true;
      transmission_stats.transmission_method = "multipart_upload";
      transmission_stats.part_size = this._multipart_size;
      transmission_stats.part_count = Math.ceil(
        content_length / this._multipart_size,
      );

      await this._transmit_large_message_http(
        file_path,
        data,
        content_length,
        target_id,
      );
    }

    // Add transmission time
    transmission_stats.transmission_time = (Date.now() - start_time) / 1000;

    // Emit event for tracking HTTP transmission usage (single comprehensive event)
    this._fire("http_transmission_stats", transmission_stats);

    console.debug(
      `HTTP message (${content_length} bytes) sent in ${(Date.now() - start_time) / 1000} s`,
    );
  }

  async _transmit_small_message_http(
    file_path,
    data,
    content_length,
    target_id,
  ) {
    // Get presigned URL for upload with 30 second TTL
    const put_url = await this._s3controller.putFile({
      file_path: file_path,
      ttl: 30,
      _rkwargs: true,
    });

    // Actually upload the content using fetch
    const response = await fetch(put_url, {
      method: "PUT",
      body: data,
      headers: {
        "Content-Type": "application/octet-stream",
      },
    });

    if (!response.ok) {
      throw new Error(
        `Failed to upload to S3: ${response.status} - ${response.statusText}`,
      );
    }

    // Get presigned URL for download
    const get_url = await this._s3controller.getFile({
      file_path: file_path,
      _rkwargs: true,
    });

    // Create the http-message envelope and send it via regular RPC
    const http_message = {
      type: "http-message",
      from: this._local_workspace
        ? `${this._local_workspace}/${this._client_id}`
        : this._client_id,
      to: target_id,
      file_path: file_path,
      presigned_url: get_url,
      content_length: content_length,
      transmission_method: "single_upload",
    };

    // Send the http-message via regular (small) RPC message
    await this._emit_message(msgpack_packb(http_message));
  }

  async _parallelUploadParts(partTasks, concurrency = null) {
    if (concurrency === null) {
      concurrency = this._max_parallel_uploads;
    }

    const results = [];
    const queue = [...partTasks]; // Create a copy of the array

    async function worker() {
      while (queue.length > 0) {
        try {
          const partTask = queue.shift();
          const result = await partTask();
          results.push(result);
        } catch (error) {
          // Re-throw the error to stop all workers
          throw error;
        }
      }
    }

    // Create worker tasks
    const workers = Array.from({ length: concurrency }, () => worker());

    // Wait for all workers to complete
    await Promise.all(workers);

    // Sort results by part number to maintain order
    results.sort((a, b) => a.part_number - b.part_number);
    return results;
  }

  async _transmit_large_message_http(
    file_path,
    data,
    content_length,
    target_id,
  ) {
    // Calculate number of parts using configurable multipart_size
    const part_size = this._multipart_size;
    const part_count = Math.ceil(content_length / part_size);

    // Start multipart upload with 30 second TTL
    const multipart_info = await this._s3controller.putFileStartMultipart({
      file_path: file_path,
      part_count: part_count,
      ttl: 30,
      _rkwargs: true,
    });

    const upload_id = multipart_info.upload_id;
    const part_urls = multipart_info.parts;

    // Create part upload tasks for parallel processing
    const partTasks = [];

    for (let part_info of part_urls) {
      const part_number = part_info.part_number;
      const part_url = part_info.url;

      // Calculate the byte range for this part
      const start_byte = (part_number - 1) * part_size;
      const end_byte = Math.min(start_byte + part_size, content_length);
      const part_data = data.slice(start_byte, end_byte);

      // Create async task for this part upload
      const uploadTask = async () => {
        const response = await fetch(part_url, {
          method: "PUT",
          body: part_data,
          headers: {
            "Content-Type": "application/octet-stream",
          },
        });

        if (!response.ok) {
          throw new Error(
            `Failed to upload part ${part_number}: ${response.status} - ${response.statusText}`,
          );
        }

        // Extract ETag for completion
        const etag =
          response.headers.get("ETag")?.replace(/"/g, "").replace(/'/g, "") ||
          "";
        return {
          part_number: part_number,
          etag: etag,
        };
      };

      partTasks.push(uploadTask);
    }

    // Upload all parts in parallel using queue-based pattern
    const uploaded_parts = await this._parallelUploadParts(partTasks);

    // Complete multipart upload
    const result = await this._s3controller.putFileCompleteMultipart({
      upload_id: upload_id,
      parts: uploaded_parts,
      _rkwargs: true,
    });

    if (!result.success) {
      throw new Error(
        `Multipart upload failed: ${result.message || "Unknown error"}`,
      );
    }

    // Get presigned URL for download
    const get_url = await this._s3controller.getFile({
      file_path: file_path,
      _rkwargs: true,
    });

    // Create the http-message envelope and send it
    const http_message = {
      type: "http-message",
      from: this._local_workspace
        ? `${this._local_workspace}/${this._client_id}`
        : this._client_id,
      to: target_id,
      file_path: file_path,
      presigned_url: get_url,
      content_length: content_length,
      transmission_method: "multipart_upload",
      part_count: part_count,
    };

    // Send the http-message via regular (small) RPC message
    await this._emit_message(msgpack_packb(http_message));
  }

  _handle_http_message(data) {
    try {
      assert(data.type === "http-message");
      assert(data.presigned_url && data.content_length !== undefined);

      // Store the original message context for reconstructed HTTP messages
      const original_context = data.ctx || {};

      const presigned_url = data.presigned_url;
      const content_length = data.content_length;

      console.info(
        `Received HTTP message notification: ${content_length} bytes at ${presigned_url}`,
      );

      // Actually download the content from the presigned URL
      const download_and_process = async () => {
        try {
          let content;
          // For large files, use parallel range requests
          if (content_length > 5 * 1024 * 1024) {
            // 5MB
            // Use parallel range requests
            const max_requests = Math.min(
              10,
              Math.floor(content_length / (1024 * 1024)) + 1,
            );
            const chunk_size = Math.floor(content_length / max_requests);

            const download_range = async (start, end) => {
              const headers = { Range: `bytes=${start}-${end}` };
              const response = await fetch(presigned_url, { headers });
              if (!response.ok) {
                throw new Error(
                  `HTTP ${response.status}: ${response.statusText}`,
                );
              }
              return response.arrayBuffer();
            };

            // Create range requests
            const tasks = [];
            for (let i = 0; i < max_requests; i++) {
              const start = i * chunk_size;
              const end =
                i === max_requests - 1
                  ? content_length - 1
                  : start + chunk_size - 1;
              tasks.push(download_range(start, end));
            }

            // Download all chunks in parallel
            const chunks = await Promise.all(tasks);
            content = new Uint8Array(content_length);
            let offset = 0;
            for (let chunk of chunks) {
              content.set(new Uint8Array(chunk), offset);
              offset += chunk.byteLength;
            }
          } else {
            // Simple download for small files
            const response = await fetch(presigned_url);
            if (!response.ok) {
              throw new Error(
                `HTTP ${response.status}: ${response.statusText}`,
              );
            }
            content = await response.arrayBuffer();
          }

          // Verify content length
          if (content.byteLength !== content_length) {
            throw new Error(
              `Content length mismatch: expected ${content_length}, got ${content.byteLength}`,
            );
          }

          // Process the downloaded content as if it came through regular message_cache
          console.debug(
            `Processing downloaded HTTP message: ${content.byteLength} bytes`,
          );

          // Unpack the message - there may be multiple segments
          const decoder = decodeMulti(new Uint8Array(content));
          const main = decoder.next().value;

          // Check if there's a second segment (extra_data)
          const extraResult = decoder.next();
          if (!extraResult.done) {
            const extra_data = extraResult.value;
            // Merge extra_data into main message
            Object.assign(main, extra_data);
          }

          // Preserve authentication context from original websocket message
          // HTTP reconstructed messages inherit auth from the triggering message
          const auth_fields = ["user", "ws"];
          for (const field of auth_fields) {
            if (field in original_context) {
              main[field] = original_context[field];
            }
          }

          // Add trusted context
          this._add_context_to_message(main);

          // Fire the event
          this._fire(main.type, main);
        } catch (e) {
          console.error(`Error downloading/processing HTTP message: ${e}`);
        }
      };

      // Schedule the download and processing
      download_and_process();
    } catch (err) {
      console.error("Error handling HTTP message:", err);
    }
  }

  _generate_uuid() {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
      /[xy]/g,
      function (c) {
        const r = (Math.random() * 16) | 0;
        const v = c == "x" ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      },
    );
  }

  reset() {
    this._event_handlers = {};
    this._services = {};
  }

  close() {
    // Clear all heartbeat intervals
    for (const session_id in this._object_store) {
      if (this._object_store.hasOwnProperty(session_id)) {
        const session = this._object_store[session_id];
        if (session && session.heartbeat_task) {
          clearInterval(session.heartbeat_task);
        }
        if (session && session.timer) {
          session.timer.clear();
        }
      }
    }
    this._fire("disconnected");
  }

  async disconnect() {
    this.close();
    await this._connection.disconnect();
  }

  async get_manager_service(config) {
    config = config || {};
    
    // Assert manager_id is set as it's guaranteed by the server
    assert(this._connection.manager_id, "Manager ID must be set");
    
    // Since the server now guarantees manager_id is available,
    // we don't need retry logic anymore
    const svc = await this.get_remote_service(
      `*/${this._connection.manager_id}:default`,
      config,
    );
    return svc;
  }

  get_all_local_services() {
    return this._services;
  }
  get_local_service(service_id, context) {
    assert(service_id);
    assert(context, "Context is required");

    const [ws, client_id] = context["to"].split("/");
    assert(
      client_id === this._client_id,
      "Services can only be accessed locally",
    );

    const service = this._services[service_id];
    if (!service) {
      throw new Error("Service not found: " + service_id);
    }

    service.config["workspace"] = context["ws"];
    // allow access for the same workspace
    if (
      service.config.visibility == "public" ||
      service.config.visibility == "unlisted"
    ) {
      return service;
    }

    // allow access for the same workspace
    if (context["ws"] === ws) {
      return service;
    }

    // Check if user is from an authorized workspace
    const authorized_workspaces = service.config.authorized_workspaces;
    if (
      authorized_workspaces &&
      authorized_workspaces.includes(context["ws"])
    ) {
      return service;
    }

    throw new Error(
      `Permission denied for getting protected service: ${service_id}, workspace mismatch: ${ws} != ${context["ws"]}`,
    );
  }
  async get_remote_service(service_uri, config) {
    let { timeout, case_conversion, kwargs_expansion } = config || {};
    timeout = timeout === undefined ? this._method_timeout : timeout;
    if (!service_uri && this._connection.manager_id) {
      service_uri = "*/" + this._connection.manager_id;
    } else if (!service_uri.includes(":")) {
      service_uri = this._client_id + ":" + service_uri;
    }
    const provider = service_uri.split(":")[0];
    let service_id = service_uri.split(":")[1];
    if (service_id.includes("@")) {
      service_id = service_id.split("@")[0];
      const app_id = service_uri.split("@")[1];
      if (this._app_id && this._app_id !== "*")
        assert(
          app_id === this._app_id,
          `Invalid app id: ${app_id} != ${this._app_id}`,
        );
    }
    assert(provider, `Invalid service uri: ${service_uri}`);

    try {
      const method = this._generate_remote_method({
        _rserver: this._server_base_url,
        _rtarget: provider,
        _rmethod: "services.built-in.get_service",
        _rpromise: true,
        _rdoc: "Get a remote service",
      });
      let svc = await waitFor(
        method(service_id),
        timeout,
        "Timeout Error: Failed to get remote service: " + service_uri,
      );
      svc.id = `${provider}:${service_id}`;
      if (kwargs_expansion) {
        svc = expandKwargs(svc);
      }
      if (case_conversion)
        return Object.assign(
          new RemoteService(),
          convertCase(svc, case_conversion),
        );
      else return Object.assign(new RemoteService(), svc);
    } catch (e) {
      console.warn("Failed to get remote service: " + service_uri, e);
      throw e;
    }
  }
  _annotate_service_methods(
    aObject,
    object_id,
    require_context,
    run_in_executor,
    visibility,
    authorized_workspaces,
  ) {
    if (typeof aObject === "function") {
      // mark the method as a remote method that requires context
      let method_name = object_id.split(".")[1];
      this._method_annotations.set(aObject, {
        require_context: Array.isArray(require_context)
          ? require_context.includes(method_name)
          : !!require_context,
        run_in_executor: run_in_executor,
        method_id: "services." + object_id,
        visibility: visibility,
        authorized_workspaces: authorized_workspaces,
      });
    } else if (aObject instanceof Array || aObject instanceof Object) {
      for (let key of Object.keys(aObject)) {
        let val = aObject[key];
        if (typeof val === "function" && val.__rpc_object__) {
          let client_id = val.__rpc_object__._rtarget;
          if (client_id.includes("/")) {
            client_id = client_id.split("/")[1];
          }
          if (this._client_id === client_id) {
            if (aObject instanceof Array) {
              aObject = aObject.slice();
            }
            // recover local method
            aObject[key] = indexObject(
              this._object_store,
              val.__rpc_object__._rmethod,
            );
            val = aObject[key]; // make sure it's annotated later
          } else {
            throw new Error(
              `Local method not found: ${val.__rpc_object__._rmethod}, client id mismatch ${this._client_id} != ${client_id}`,
            );
          }
        }
        this._annotate_service_methods(
          val,
          object_id + "." + key,
          require_context,
          run_in_executor,
          visibility,
          authorized_workspaces,
        );
      }
    }
  }
  add_service(api, overwrite) {
    if (!api || Array.isArray(api)) throw new Error("Invalid service object");
    if (api.constructor === Object) {
      api = Object.assign({}, api);
    } else {
      const normApi = {};
      const props = Object.getOwnPropertyNames(api).concat(
        Object.getOwnPropertyNames(Object.getPrototypeOf(api)),
      );
      for (let k of props) {
        if (k !== "constructor") {
          if (typeof api[k] === "function") normApi[k] = api[k].bind(api);
          else normApi[k] = api[k];
        }
      }
      // For class instance, we need set a default id
      api.id = api.id || "default";
      api = normApi;
    }
    assert(
      api.id && typeof api.id === "string",
      `Service id not found: ${api}`,
    );
    if (!api.name) {
      api.name = api.id;
    }
    if (!api.config) {
      api.config = {};
    }
    if (!api.type) {
      api.type = "generic";
    }
    // require_context only applies to the top-level functions
    let require_context = false,
      run_in_executor = false;
    if (api.config.require_context)
      require_context = api.config.require_context;
    if (api.config.run_in_executor) run_in_executor = true;
    const visibility = api.config.visibility || "protected";
    assert(["protected", "public", "unlisted"].includes(visibility));

    // Validate authorized_workspaces
    const authorized_workspaces = api.config.authorized_workspaces;
    if (authorized_workspaces !== undefined) {
      if (visibility !== "protected") {
        throw new Error(
          `authorized_workspaces can only be set when visibility is 'protected', got visibility='${visibility}'`,
        );
      }
      if (!Array.isArray(authorized_workspaces)) {
        throw new Error(
          "authorized_workspaces must be an array of workspace ids",
        );
      }
      for (const ws_id of authorized_workspaces) {
        if (typeof ws_id !== "string") {
          throw new Error(
            `Each workspace id in authorized_workspaces must be a string, got ${typeof ws_id}`,
          );
        }
      }
    }
    this._annotate_service_methods(
      api,
      api["id"],
      require_context,
      run_in_executor,
      visibility,
      authorized_workspaces,
    );

    if (this._services[api.id]) {
      if (overwrite) {
        delete this._services[api.id];
      } else {
        throw new Error(
          `Service already exists: ${api.id}, please specify a different id (not ${api.id}) or overwrite=true`,
        );
      }
    }
    this._services[api.id] = api;
    return api;
  }

  _extract_service_info(service) {
    const config = service.config || {};
    config.workspace =
      config.workspace || this._local_workspace || this._connection._workspace;
    const skipContext = config.require_context;
    const excludeKeys = ["id", "config", "name", "description", "type", "docs", "app_id", "service_schema"]
    const filteredService = {};
    for (const key of Object.keys(service)) {
      if (!excludeKeys.includes(key)) {
        filteredService[key] = service[key];
      }
    }
    const serviceSchema = _get_schema(filteredService, null, skipContext);
    const serviceInfo = {
      config: config,
      id: `${config.workspace}/${this._client_id}:${service["id"]}`,
      name: service.name || service["id"],
      description: service.description || "",
      type: service.type || "generic",
      docs: service.docs || null,
      app_id: this._app_id,
      service_schema: serviceSchema,
    };
    return serviceInfo;
  }

  async get_service_schema(service) {
    const skipContext = service.config.require_context;
    return _get_schema(service, null, skipContext);
  }

  async register_service(api, config) {
    let { check_type, notify, overwrite } = config || {};
    notify = notify === undefined ? true : notify;
    let manager;
    if (check_type && api.type) {
      try {
        manager = await this.get_manager_service({
          timeout: 10,
          case_conversion: "camel",
        });
        const type_info = await manager.get_service_type(api.type);
        api = _annotate_service(api, type_info);
      } catch (e) {
        throw new Error(`Failed to get service type ${api.type}, error: ${e}`);
      }
    }

    const service = this.add_service(api, overwrite);
    const serviceInfo = this._extract_service_info(service);
    if (notify) {
      try {
        manager =
          manager ||
          (await this.get_manager_service({
            timeout: 10,
            case_conversion: "snake",
          }));
        await manager.register_service(serviceInfo);
      } catch (e) {
        throw new Error(`Failed to notify workspace manager: ${e}`);
      }
    }
    return serviceInfo;
  }

  async unregister_service(service, notify) {
    notify = notify === undefined ? true : notify;
    let service_id;
    if (typeof service === "string") {
      service_id = service;
    } else {
      service_id = service.id;
    }
    assert(
      service_id && typeof service_id === "string",
      `Invalid service id: ${service_id}`,
    );
    if (service_id.includes(":")) {
      service_id = service_id.split(":")[1];
    }
    if (service_id.includes("@")) {
      service_id = service_id.split("@")[0];
    }
    if (!this._services[service_id]) {
      throw new Error(`Service not found: ${service_id}`);
    }
    if (notify) {
      const manager = await this.get_manager_service({
        timeout: 10,
        case_conversion: "snake",
      });
      await manager.unregister_service(service_id);
    }
    delete this._services[service_id];
  }

  _ndarray(typedArray, shape, dtype) {
    const _dtype = typedArrayToDtype(typedArray);
    if (dtype && dtype !== _dtype) {
      throw (
        "dtype doesn't match the type of the array: " + _dtype + " != " + dtype
      );
    }
    shape = shape || [typedArray.length];
    return {
      _rtype: "ndarray",
      _rvalue: typedArray.buffer,
      _rshape: shape,
      _rdtype: _dtype,
    };
  }

  _encode_callback(
    name,
    callback,
    session_id,
    clear_after_called,
    timer,
    local_workspace,
    description,
  ) {
    let method_id = `${session_id}.${name}`;
    let encoded = {
      _rtype: "method",
      _rtarget: local_workspace
        ? `${local_workspace}/${this._client_id}`
        : this._client_id,
      _rmethod: method_id,
      _rpromise: false,
    };

    const self = this;
    let wrapped_callback = function () {
      try {
        callback.apply(null, Array.prototype.slice.call(arguments));
      } catch (error) {
        console.error(
          `Error in callback(${method_id}, ${description}): ${error}`,
        );
      } finally {
        // Clear the timer first if it exists
        if (timer && timer.started) {
          timer.clear();
        }

        // Clean up the entire session when resolve/reject is called
        if (clear_after_called && self._object_store[session_id]) {
          // For promise callbacks (resolve/reject), clean up the entire session
          if (name === "resolve" || name === "reject") {
            delete self._object_store[session_id];
          } else {
            // For other callbacks, just clean up this specific callback
            self._cleanup_session_if_needed(session_id, name);
          }
        }
      }
    };
    wrapped_callback.__name__ = `callback(${method_id})`;
    return [encoded, wrapped_callback];
  }

  _cleanup_session_if_needed(session_id, callback_name) {
    /**
     * Clean session management - all logic in one place.
     */
    if (!session_id) {
      console.debug("Cannot cleanup session: session_id is empty");
      return;
    }

    try {
      const store = this._get_session_store(session_id, false);
      if (!store) {
        console.debug(`Session ${session_id} not found for cleanup`);
        return;
      }

      let should_cleanup = false;

      // Promise sessions: let the promise manager decide cleanup
      if (store._promise_manager) {
        try {
          const promise_manager = store._promise_manager;
          if (
            promise_manager.should_cleanup_on_callback &&
            promise_manager.should_cleanup_on_callback(callback_name)
          ) {
            if (promise_manager.settle) {
              promise_manager.settle();
            }
            should_cleanup = true;
            console.debug(
              `Promise session ${session_id} settled and marked for cleanup`,
            );
          }
        } catch (e) {
          console.warn(
            `Error in promise manager cleanup for session ${session_id}:`,
            e,
          );
        }
      } else {
        // Regular sessions: only cleanup temporary method call sessions
        // Don't cleanup service registration sessions or persistent sessions
        // Only cleanup sessions that are clearly temporary promises for method calls
        if (
          (callback_name === "resolve" || callback_name === "reject") &&
          store._callbacks &&
          Object.keys(store._callbacks).includes(callback_name)
        ) {
          should_cleanup = true;
          console.debug(
            `Regular session ${session_id} marked for cleanup after ${callback_name}`,
          );
        }
      }

      if (should_cleanup) {
        this._cleanup_session_completely(session_id);
      }
    } catch (error) {
      console.warn(`Error during session cleanup for ${session_id}:`, error);
    }
  }

  _cleanup_session_completely(session_id) {
    /**
     * Complete session cleanup with resource management.
     */
    try {
      const store = this._get_session_store(session_id, false);
      if (!store) {
        console.debug(`Session ${session_id} already cleaned up`);
        return;
      }

      // Clean up resources before removing session
      if (store.timer && typeof store.timer.clear === "function") {
        try {
          store.timer.clear();
        } catch (error) {
          console.warn(
            `Error clearing timer for session ${session_id}:`,
            error,
          );
        }
      }

      if (
        store.heartbeat_task &&
        typeof store.heartbeat_task.cancel === "function"
      ) {
        try {
          store.heartbeat_task.cancel();
        } catch (error) {
          console.warn(
            `Error canceling heartbeat for session ${session_id}:`,
            error,
          );
        }
      }

      // Navigate and clean session path
      const levels = session_id.split(".");
      let current_store = this._object_store;

      // Navigate to parent of target level
      for (let i = 0; i < levels.length - 1; i++) {
        const level = levels[i];
        if (!current_store[level]) {
          console.debug(
            `Session path ${session_id} not found at level ${level}`,
          );
          return;
        }
        current_store = current_store[level];
      }

      // Delete the final level
      const final_key = levels[levels.length - 1];
      if (current_store[final_key]) {
        delete current_store[final_key];
        console.debug(`Cleaned up session ${session_id}`);

        // Clean up empty parent containers
        this._cleanup_empty_containers(levels.slice(0, -1));
      }
    } catch (error) {
      console.warn(
        `Error in complete session cleanup for ${session_id}:`,
        error,
      );
    }
  }

  _cleanup_empty_containers(path_levels) {
    /**
     * Clean up empty parent containers to prevent memory leaks.
     */
    try {
      // Work backwards from the deepest level
      for (let depth = path_levels.length - 1; depth >= 0; depth--) {
        let current_store = this._object_store;

        // Navigate to parent of current depth
        for (let i = 0; i < depth; i++) {
          current_store = current_store[path_levels[i]];
          if (!current_store) return; // Path doesn't exist
        }

        // Check if container at current depth is empty
        const container_key = path_levels[depth];
        const container = current_store[container_key];

        if (
          container &&
          typeof container === "object" &&
          Object.keys(container).length === 0
        ) {
          delete current_store[container_key];
          console.debug(
            `Cleaned up empty container at depth ${depth}: ${path_levels.slice(0, depth + 1).join(".")}`,
          );
        } else {
          // Container is not empty, stop cleanup
          break;
        }
      }
    } catch (error) {
      console.warn("Error cleaning up empty containers:", error);
    }
  }

  get_session_stats() {
    /**
     * Get detailed session statistics.
     */
    const stats = {
      total_sessions: 0,
      promise_sessions: 0,
      regular_sessions: 0,
      sessions_with_timers: 0,
      sessions_with_heartbeat: 0,
      system_stores: {},
      session_ids: [],
      memory_usage: 0,
    };

    if (!this._object_store) {
      return stats;
    }

    for (const key in this._object_store) {
      const value = this._object_store[key];

      if (["services", "message_cache"].includes(key)) {
        // System stores - don't count these as sessions
        stats.system_stores[key] = {
          size:
            typeof value === "object" && value ? Object.keys(value).length : 0,
        };
        continue;
      }

      // Count all non-system non-empty objects as sessions
      if (value && typeof value === "object") {
        const sessionKeys = Object.keys(value);

        // Only skip completely empty objects
        if (sessionKeys.length > 0) {
          stats.total_sessions++;
          stats.session_ids.push(key);

          if (value._promise_manager) {
            stats.promise_sessions++;
          } else {
            stats.regular_sessions++;
          }

          if (value._timer || value.timer) stats.sessions_with_timers++;
          if (value._heartbeat || value.heartbeat)
            stats.sessions_with_heartbeat++;

          // Estimate memory usage
          stats.memory_usage += JSON.stringify(value).length;
        }
      }
    }

    return stats;
  }

  _force_cleanup_all_sessions() {
    /**
     * Force cleanup all sessions (for testing purposes).
     */
    if (!this._object_store) {
      console.debug("Force cleaning up 0 sessions");
      return;
    }

    let cleaned_count = 0;
    const keys_to_delete = [];

    for (const key in this._object_store) {
      // Don't delete system stores
      if (!["services", "message_cache"].includes(key)) {
        const value = this._object_store[key];
        if (
          value &&
          typeof value === "object" &&
          Object.keys(value).length > 0
        ) {
          keys_to_delete.push(key);
          cleaned_count++;
        }
      }
    }

    // Delete the sessions
    for (const key of keys_to_delete) {
      delete this._object_store[key];
    }

    console.debug(`Force cleaning up ${cleaned_count} sessions`);
  }

  // Clean helper to identify promise method calls by session type
  _is_promise_method_call(method_path) {
    const session_id = method_path.split(".")[0];
    const session = this._get_session_store(session_id, false);
    return session && session._promise_manager;
  }

  // Simplified Promise Manager - enhanced version
  _create_promise_manager() {
    /**
     * Create a promise manager to track promise state and decide cleanup.
     */
    return {
      should_cleanup_on_callback: (callback_name) => {
        return ["resolve", "reject"].includes(callback_name);
      },
      settle: () => {
        // Promise is settled (resolved or rejected)
        console.debug("Promise settled");
      },
    };
  }

  async _encode_promise(
    resolve,
    reject,
    session_id,
    clear_after_called,
    timer,
    local_workspace,
    description,
  ) {
    let store = this._get_session_store(session_id, true);
    if (!store) {
      console.warn(
        `Failed to create session store ${session_id}, session management may be impaired`,
      );
      store = {};
    }

    // Clean promise lifecycle management - TYPE-BASED, not string-based
    store._promise_manager = this._create_promise_manager();

    let encoded = {};

    if (timer && reject && this._method_timeout) {
      [encoded.heartbeat, store.heartbeat] = this._encode_callback(
        "heartbeat",
        timer.reset.bind(timer),
        session_id,
        false,
        null,
        local_workspace,
      );
      store.timer = timer;
      encoded.interval = this._method_timeout / 2;
    } else {
      timer = null;
    }

    [encoded.resolve, store.resolve] = this._encode_callback(
      "resolve",
      resolve,
      session_id,
      clear_after_called,
      timer,
      local_workspace,
      `resolve (${description})`,
    );
    [encoded.reject, store.reject] = this._encode_callback(
      "reject",
      reject,
      session_id,
      clear_after_called,
      timer,
      local_workspace,
      `reject (${description})`,
    );
    return encoded;
  }

  async _send_chunks(data, target_id, session_id) {
    // Try HTTP message transmission first if available and message is large enough
    if (
      this._enable_http_transmission &&
      data.length >= this._http_transmission_threshold
    ) {
      // Check if connection is ready (not in reconnection state)
      let connection_ready = false;
      if (this._connection && this._connection._websocket) {
        // Check if websocket is in OPEN state
        connection_ready =
          this._connection._websocket.readyState === WebSocket.OPEN;
      }

      if (connection_ready) {
        if (!this._http_message_transmission_available) {
          // Attempt to initialize HTTP transmission if not yet attempted
          try {
            await this._initialize_http_message_transmission();
          } catch (error) {
            // Initialization failed, fall back to regular chunking
            console.debug(
              "Failed to initialize HTTP message transmission:",
              error.message,
            );
          }
        }

        if (this._http_message_transmission_available) {
          try {
            // Use HTTP transmission for large messages
            await this._send_chunks_http(data, target_id, session_id);
            return;
          } catch (error) {
            console.warn(
              "Failed to send message via HTTP, falling back to WebSocket chunking:",
              error.message,
            );
            // Fall through to regular WebSocket chunking
          }
        }
      }
    }

    // 1) Get the remote service
    const remote_services = await this.get_remote_service(
      `${target_id}:built-in`,
    );
    if (!remote_services.message_cache) {
      throw new Error(
        "Remote client does not support message caching for large messages.",
      );
    }

    const message_cache = remote_services.message_cache;
    const message_id = session_id || randId();
    const total_size = data.length;
    const start_time = Date.now(); // measure time
    const chunk_num = Math.ceil(total_size / this._long_message_chunk_size);
    if (remote_services.config.api_version >= 3) {
      await message_cache.create(message_id, !!session_id);
      const semaphore = new Semaphore(CONCURRENCY_LIMIT);

      const tasks = [];
      for (let idx = 0; idx < chunk_num; idx++) {
        const startByte = idx * this._long_message_chunk_size;
        const chunk = data.slice(
          startByte,
          startByte + this._long_message_chunk_size,
        );

        const taskFn = async () => {
          await message_cache.set(message_id, idx, chunk, !!session_id);
          // console.debug(
          //   `Sending chunk ${idx + 1}/${chunk_num} (total=${total_size} bytes)`,
          // );
        };

        // Push into an array, each one runs under the semaphore
        tasks.push(semaphore.run(taskFn));
      }

      // Wait for all chunk uploads to finish
      try {
        await Promise.all(tasks);
      } catch (error) {
        // If any chunk fails, clean up the message cache
        try {
          await message_cache.remove(message_id);
        } catch (cleanupError) {
          console.error(
            `Failed to clean up message cache after error: ${cleanupError}`,
          );
        }
        throw error;
      }
    } else {
      // 3) Legacy version (sequential appends):
      await message_cache.create(message_id, !!session_id);
      for (let idx = 0; idx < chunk_num; idx++) {
        const startByte = idx * this._long_message_chunk_size;
        const chunk = data.slice(
          startByte,
          startByte + this._long_message_chunk_size,
        );
        await message_cache.append(message_id, chunk, !!session_id);
        // console.debug(
        //   `Sending chunk ${idx + 1}/${chunk_num} (total=${total_size} bytes)`,
        // );
      }
    }
    await message_cache.process(message_id, !!session_id);
    const durationSec = ((Date.now() - start_time) / 1000).toFixed(2);
    // console.debug(`All chunks (${total_size} bytes) sent in ${durationSec} s`);
  }

  emit(main_message, extra_data) {
    assert(
      typeof main_message === "object" && main_message.type,
      "Invalid message, must be an object with a `type` fields.",
    );
    if (!main_message.to) {
      this._fire(main_message.type, main_message);
      return;
    }
    let message_package = msgpack_packb(main_message);
    if (extra_data) {
      const extra = msgpack_packb(extra_data);
      message_package = new Uint8Array([...message_package, ...extra]);
    }
    const total_size = message_package.length;
    if (total_size > this._long_message_chunk_size + 1024) {
      console.warn(`Sending large message (size=${total_size})`);
    }
    return this._emit_message(message_package);
  }

  _generate_remote_method(
    encoded_method,
    remote_parent,
    local_parent,
    remote_workspace,
    local_workspace,
  ) {
    let target_id = encoded_method._rtarget;
    if (remote_workspace && !target_id.includes("/")) {
      if (remote_workspace !== target_id) {
        target_id = remote_workspace + "/" + target_id;
      }
      // Fix the target id to be an absolute id
      encoded_method._rtarget = target_id;
    }
    let method_id = encoded_method._rmethod;
    let with_promise = encoded_method._rpromise || false;
    const description = `method: ${method_id}, docs: ${encoded_method._rdoc}`;
    const self = this;

    function remote_method() {
      return new Promise(async (resolve, reject) => {
        let local_session_id = randId();
        if (local_parent) {
          // Store the children session under the parent
          local_session_id = local_parent + "." + local_session_id;
        }
        let store = self._get_session_store(local_session_id, true);
        if (!store) {
          reject(
            new Error(
              `Runtime Error: Failed to get session store ${local_session_id} (context: ${description})`,
            ),
          );
          return;
        }
        store["target_id"] = target_id;
        const args = await self._encode(
          Array.prototype.slice.call(arguments),
          local_session_id,
          local_workspace,
        );
        const argLength = args.length;
        // if the last argument is an object, mark it as kwargs
        const withKwargs =
          argLength > 0 &&
          typeof args[argLength - 1] === "object" &&
          args[argLength - 1] !== null &&
          args[argLength - 1]._rkwargs;
        if (withKwargs) delete args[argLength - 1]._rkwargs;

        let from_client;
        if (!self._local_workspace) {
          from_client = self._client_id;
        } else {
          from_client = self._local_workspace + "/" + self._client_id;
        }

        let main_message = {
          type: "method",
          from: from_client,
          to: target_id,
          method: method_id,
        };
        let extra_data = {};
        if (args) {
          extra_data["args"] = args;
        }
        if (withKwargs) {
          extra_data["with_kwargs"] = withKwargs;
        }

        // console.log(
        //   `Calling remote method ${target_id}:${method_id}, session: ${local_session_id}`
        // );
        if (remote_parent) {
          // Set the parent session
          // Note: It's a session id for the remote, not the current client
          main_message["parent"] = remote_parent;
        }

        let timer = null;
        if (with_promise) {
          // Only pass the current session id to the remote
          // if we want to received the result
          // I.e. the session id won't be passed for promises themselves
          main_message["session"] = local_session_id;
          let method_name = `${target_id}:${method_id}`;
          
          // Create a timer that gets reset by heartbeat
          // Methods can run indefinitely as long as heartbeat keeps resetting the timer
          // IMPORTANT: When timeout occurs, we must clean up the session to prevent memory leaks
          const timeoutCallback = function(error_msg) {
            // First reject the promise
            reject(error_msg);
            // Then clean up the entire session to stop all callbacks
            if (self._object_store[local_session_id]) {
              delete self._object_store[local_session_id];
              console.debug(`Cleaned up session ${local_session_id} after timeout`);
            }
          };
          
          timer = new Timer(
            self._method_timeout,
            timeoutCallback,
            [`Method call timed out: ${method_name}, context: ${description}`],
            method_name,
          );
          // By default, hypha will clear the session after the method is called
          // However, if the args contains _rintf === true, we will not clear the session

          // Helper function to recursively check for _rintf objects
          function hasInterfaceObject(obj) {
            if (!obj || typeof obj !== "object") return false;
            if (obj._rintf === true) return true;
            if (Array.isArray(obj)) {
              return obj.some((item) => hasInterfaceObject(item));
            }
            if (obj.constructor === Object) {
              return Object.values(obj).some((value) =>
                hasInterfaceObject(value),
              );
            }
            return false;
          }

          let clear_after_called = !hasInterfaceObject(args);

          const promiseData = await self._encode_promise(
            resolve,
            reject,
            local_session_id,
            clear_after_called,
            timer,
            local_workspace,
            description,
          );

          if (with_promise === true) {
            extra_data["promise"] = promiseData;
          } else if (with_promise === "*") {
            extra_data["promise"] = "*";
            extra_data["t"] = self._method_timeout / 2;
          } else {
            throw new Error(`Unsupported promise type: ${with_promise}`);
          }
        }
        // The message consists of two segments, the main message and extra data
        let message_package = msgpack_packb(main_message);
        if (extra_data) {
          const extra = msgpack_packb(extra_data);
          message_package = new Uint8Array([...message_package, ...extra]);
        }
        const total_size = message_package.length;
        if (
          total_size <= self._long_message_chunk_size + 1024 ||
          remote_method.__no_chunk__
        ) {
          self
            ._emit_message(message_package)
            .then(function () {
              if (timer) {
                // Start the timer after message is sent successfully
                timer.start();
              }
            })
            .catch(function (err) {
              const error_msg = `Failed to send the request when calling method (${target_id}:${method_id}), error: ${err}`;
              if (reject) {
                reject(new Error(error_msg));
              } else {
                // No reject callback available, log the error to prevent unhandled promise rejections
                console.warn("Unhandled RPC method call error:", error_msg);
              }
              if (timer) {
                timer.clear();
              }
            });
        } else {
          // send chunk by chunk
          self
            ._send_chunks(message_package, target_id, remote_parent)
            .then(function () {
              if (timer) {
                // Start the timer after message is sent successfully
                timer.start();
              }
            })
            .catch(function (err) {
              const error_msg = `Failed to send the request when calling method (${target_id}:${method_id}), error: ${err}`;
              if (reject) {
                reject(new Error(error_msg));
              } else {
                // No reject callback available, log the error to prevent unhandled promise rejections
                console.warn("Unhandled RPC method call error:", error_msg);
              }
              if (timer) {
                timer.clear();
              }
            });
        }
      });
    }

    // Generate debugging information for the method
    remote_method.__rpc_object__ = encoded_method;
    const parts = method_id.split(".");

    remote_method.__name__ = encoded_method._rname || parts[parts.length - 1];
    if (remote_method.__name__.includes("#")) {
      remote_method.__name__ = remote_method.__name__.split("#")[1];
    }
    remote_method.__doc__ =
      encoded_method._rdoc || `Remote method: ${method_id}`;
    remote_method.__schema__ = encoded_method._rschema;
    // Prevent circular chunk sending
    remote_method.__no_chunk__ =
      encoded_method._rmethod === "services.built-in.message_cache.append";
    return remote_method;
  }

  get_client_info() {
    const services = [];
    for (let service of Object.values(this._services)) {
      services.push(this._extract_service_info(service));
    }

    return {
      id: this._client_id,
      services: services,
    };
  }

  async _handle_method(data) {
    let reject = null;
    let heartbeat_task = null;
    try {
      assert(data.method && data.ctx && data.from);
      const method_name = data.from + ":" + data.method;
      const remote_workspace = data.from.split("/")[0];
      const remote_client_id = data.from.split("/")[1];
      // Make sure the target id is an absolute id
      data["to"] = data["to"].includes("/")
        ? data["to"]
        : remote_workspace + "/" + data["to"];
      data["ctx"]["to"] = data["to"];
      let local_workspace;
      if (!this._local_workspace) {
        local_workspace = data["to"].split("/")[0];
      } else {
        if (this._local_workspace && this._local_workspace !== "*") {
          assert(
            data["to"].split("/")[0] === this._local_workspace,
            "Workspace mismatch: " +
              data["to"].split("/")[0] +
              " != " +
              this._local_workspace,
          );
        }
        local_workspace = this._local_workspace;
      }
      const local_parent = data.parent;

      let resolve, reject;
      if (data.promise) {
        // Decode the promise with the remote session id
        // Such that the session id will be passed to the remote as a parent session id
        const promise = await this._decode(
          data.promise === "*" ? this._expand_promise(data) : data.promise,
          data.session,
          local_parent,
          remote_workspace,
          local_workspace,
        );
        resolve = promise.resolve;
        reject = promise.reject;
        if (promise.heartbeat && promise.interval) {
          async function heartbeat() {
            try {
              // console.debug("Reset heartbeat timer: " + data.method);
              await promise.heartbeat();
            } catch (err) {
              console.error(err);
            }
          }
          heartbeat_task = setInterval(heartbeat, promise.interval * 1000);
          // Store the heartbeat task in the session store for cleanup
          if (data.session) {
            const session_store = this._get_session_store(data.session, false);
            if (session_store) {
              session_store.heartbeat_task = heartbeat_task;
            }
          }
        }
      }

      let method;

      try {
        method = indexObject(this._object_store, data["method"]);
      } catch (e) {
        // Clean promise method detection - TYPE-BASED, not string-based
        if (this._is_promise_method_call(data["method"])) {
          console.debug(
            `Promise method ${data["method"]} not available (detected by session type), ignoring: ${method_name}`,
          );
          return;
        }

        // Check if this is a session-based method call that might have expired
        const method_parts = data["method"].split(".");
        if (method_parts.length > 1) {
          const session_id = method_parts[0];
          // Check if the session exists but the specific method doesn't
          if (session_id in this._object_store) {
            console.debug(
              `Session ${session_id} exists but method ${data["method"]} not found, likely expired callback: ${method_name}`,
            );
            // For expired callbacks, don't throw an exception, just log and return
            if (typeof reject === "function") {
              reject(new Error(`Method expired or not found: ${method_name}`));
            }
            return;
          } else {
            console.debug(
              `Session ${session_id} not found for method ${data["method"]}, likely cleaned up: ${method_name}`,
            );
            // For cleaned up sessions, just log and return without throwing
            if (typeof reject === "function") {
              reject(new Error(`Session not found: ${method_name}`));
            }
            return;
          }
        }

        console.debug(
          `Failed to find method ${method_name} at ${this._client_id}`,
        );
        const error = new Error(
          `Method not found: ${method_name} at ${this._client_id}`,
        );
        if (typeof reject === "function") {
          reject(error);
        } else {
          // Log the error instead of throwing to prevent unhandled exceptions
          console.warn(
            "Method not found and no reject callback:",
            error.message,
          );
        }
        return;
      }

      assert(
        method && typeof method === "function",
        "Invalid method: " + method_name,
      );

      // Check permission
      if (this._method_annotations.has(method)) {
        // For services, it should not be protected
        if (this._method_annotations.get(method).visibility === "protected") {
          // Allow access from same workspace
          if (local_workspace === remote_workspace) {
            // Access granted
          }
          // Check if remote workspace is in authorized_workspaces list
          else if (
            this._method_annotations.get(method).authorized_workspaces &&
            this._method_annotations
              .get(method)
              .authorized_workspaces.includes(remote_workspace)
          ) {
            // Access granted
          }
          // Allow manager access
          else if (
            remote_workspace === "*" &&
            remote_client_id === this._connection.manager_id
          ) {
            // Access granted
          } else {
            throw new Error(
              "Permission denied for invoking protected method " +
                method_name +
                ", workspace mismatch: " +
                local_workspace +
                " != " +
                remote_workspace,
            );
          }
        }
      } else {
        // For sessions, the target_id should match exactly
        let session_target_id =
          this._object_store[data.method.split(".")[0]].target_id;
        if (
          local_workspace === remote_workspace &&
          session_target_id &&
          session_target_id.indexOf("/") === -1
        ) {
          session_target_id = local_workspace + "/" + session_target_id;
        }
        if (session_target_id !== data.from) {
          throw new Error(
            "Access denied for method call (" +
              method_name +
              ") from " +
              data.from +
              " to target " +
              session_target_id,
          );
        }
      }

      // Make sure the parent session is still open
      if (local_parent) {
        // The parent session should be a session that generate the current method call
        assert(
          this._get_session_store(local_parent, true) !== null,
          "Parent session was closed: " + local_parent,
        );
      }
      let args;
      if (data.args) {
        args = await this._decode(
          data.args,
          data.session,
          null,
          remote_workspace,
          null,
        );
      } else {
        args = [];
      }
      if (
        this._method_annotations.has(method) &&
        this._method_annotations.get(method).require_context
      ) {
        // if args.length + 1 is less than the required number of arguments we will pad with undefined
        // so we make sure the last argument is the context
        if (args.length + 1 < method.length) {
          for (let i = args.length; i < method.length - 1; i++) {
            args.push(undefined);
          }
        }
        args.push(data.ctx);
        assert(
          args.length === method.length,
          `Runtime Error: Invalid number of arguments for method ${method_name}, expected ${method.length} but got ${args.length}`,
        );
      }
      // console.debug(`Executing method: ${method_name} (${data.method})`);
      if (data.promise) {
        const result = method.apply(null, args);
        if (result instanceof Promise) {
          result
            .then((result) => {
              resolve(result);
              clearInterval(heartbeat_task);
            })
            .catch((err) => {
              reject(err);
              clearInterval(heartbeat_task);
            });
        } else {
          resolve(result);
          clearInterval(heartbeat_task);
        }
      } else {
        method.apply(null, args);
        clearInterval(heartbeat_task);
      }
    } catch (err) {
      if (reject) {
        reject(err);
        // console.debug("Error during calling method: ", err);
      } else {
        console.error("Error during calling method: ", err);
      }
      // make sure we clear the heartbeat timer
      clearInterval(heartbeat_task);
    }
  }

  encode(aObject, session_id) {
    return this._encode(aObject, session_id);
  }

  _get_session_store(session_id, create) {
    if (!session_id) {
      return null;
    }
    let store = this._object_store;
    const levels = session_id.split(".");
    if (create) {
      const last_index = levels.length - 1;
      for (let level of levels.slice(0, last_index)) {
        if (!store[level]) {
          // Instead of returning null, create intermediate sessions as needed
          store[level] = {};
        }
        store = store[level];
      }
      // Create the last level
      if (!store[levels[last_index]]) {
        store[levels[last_index]] = {};
      }
      return store[levels[last_index]];
    } else {
      for (let level of levels) {
        if (!store[level]) {
          return null;
        }
        store = store[level];
      }
      return store;
    }
  }

  /**
   * Prepares the provided set of remote method arguments for
   * sending to the remote site, replaces all the callbacks with
   * identifiers
   *
   * @param {Array} args to wrap
   *
   * @returns {Array} wrapped arguments
   */
  async _encode(aObject, session_id, local_workspace) {
    const aType = typeof aObject;
    if (
      aType === "number" ||
      aType === "string" ||
      aType === "boolean" ||
      aObject === null ||
      aObject === undefined ||
      aObject instanceof Uint8Array
    ) {
      return aObject;
    }
    if (aObject instanceof ArrayBuffer) {
      return {
        _rtype: "memoryview",
        _rvalue: new Uint8Array(aObject),
      };
    }
    // Reuse the remote object
    if (aObject.__rpc_object__) {
      const _server = aObject.__rpc_object__._rserver || this._server_base_url;
      if (_server === this._server_base_url) {
        return aObject.__rpc_object__;
      } // else {
      //   console.debug(
      //     `Encoding remote function from a different server ${_server}, current server: ${this._server_base_url}`,
      //   );
      // }
    }

    let bObject;

    // skip if already encoded
    if (aObject.constructor instanceof Object && aObject._rtype) {
      // make sure the interface functions are encoded
      const temp = aObject._rtype;
      delete aObject._rtype;
      bObject = await this._encode(aObject, session_id, local_workspace);
      bObject._rtype = temp;
      return bObject;
    }

    if (isGenerator(aObject) || isAsyncGenerator(aObject)) {
      // Handle generator functions and generator objects
      assert(
        session_id && typeof session_id === "string",
        "Session ID is required for generator encoding",
      );
      const object_id = randId();

      // Get the session store
      const store = this._get_session_store(session_id, true);
      assert(
        store !== null,
        `Failed to create session store ${session_id} due to invalid parent`,
      );

      // Check if it's an async generator
      const isAsync = isAsyncGenerator(aObject);

      // Define method to get next item from the generator
      const nextItemMethod = async () => {
        if (isAsync) {
          const iterator = aObject;
          const result = await iterator.next();
          if (result.done) {
            delete store[object_id];
            return { _rtype: "stop_iteration" };
          }
          return result.value;
        } else {
          const iterator = aObject;
          const result = iterator.next();
          if (result.done) {
            delete store[object_id];
            return { _rtype: "stop_iteration" };
          }
          return result.value;
        }
      };

      // Store the next_item method in the session
      store[object_id] = nextItemMethod;

      // Create a method that will be used to fetch the next item from the generator
      bObject = {
        _rtype: "generator",
        _rserver: this._server_base_url,
        _rtarget: this._client_id,
        _rmethod: `${session_id}.${object_id}`,
        _rpromise: "*",
        _rdoc: "Remote generator",
      };
      return bObject;
    } else if (typeof aObject === "function") {
      if (this._method_annotations.has(aObject)) {
        let annotation = this._method_annotations.get(aObject);
        bObject = {
          _rtype: "method",
          _rserver: this._server_base_url,
          _rtarget: this._client_id,
          _rmethod: annotation.method_id,
          _rpromise: "*",
          _rname: aObject.name,
        };
      } else {
        assert(typeof session_id === "string");
        let object_id;
        if (aObject.__name__) {
          object_id = `${randId()}#${aObject.__name__}`;
        } else {
          object_id = randId();
        }
        bObject = {
          _rtype: "method",
          _rserver: this._server_base_url,
          _rtarget: this._client_id,
          _rmethod: `${session_id}.${object_id}`,
          _rpromise: "*",
          _rname: aObject.name,
        };
        let store = this._get_session_store(session_id, true);
        assert(
          store !== null,
          `Failed to create session store ${session_id} due to invalid parent`,
        );
        store[object_id] = aObject;
      }
      bObject._rdoc = aObject.__doc__;
      if (!bObject._rdoc) {
        try {
          const funcInfo = getFunctionInfo(aObject);
          if (funcInfo && !bObject._rdoc) {
            bObject._rdoc = `${funcInfo.doc}`;
          }
        } catch (e) {
          console.error("Failed to extract function docstring:", aObject);
        }
      }
      bObject._rschema = aObject.__schema__;
      return bObject;
    }
    const isarray = Array.isArray(aObject);

    for (let tp of Object.keys(this._codecs)) {
      const codec = this._codecs[tp];
      if (codec.encoder && aObject instanceof codec.type) {
        // TODO: what if multiple encoders found
        let encodedObj = await Promise.resolve(codec.encoder(aObject));
        if (encodedObj && !encodedObj._rtype) encodedObj._rtype = codec.name;
        // encode the functions in the interface object
        if (typeof encodedObj === "object") {
          const temp = encodedObj._rtype;
          delete encodedObj._rtype;
          encodedObj = await this._encode(
            encodedObj,
            session_id,
            local_workspace,
          );
          encodedObj._rtype = temp;
        }
        bObject = encodedObj;
        return bObject;
      }
    }

    if (
      /*global tf*/
      typeof tf !== "undefined" &&
      tf.Tensor &&
      aObject instanceof tf.Tensor
    ) {
      const v_buffer = aObject.dataSync();
      bObject = {
        _rtype: "ndarray",
        _rvalue: new Uint8Array(v_buffer.buffer),
        _rshape: aObject.shape,
        _rdtype: aObject.dtype,
      };
    } else if (
      /*global nj*/
      typeof nj !== "undefined" &&
      nj.NdArray &&
      aObject instanceof nj.NdArray
    ) {
      if (!aObject.selection || !aObject.selection.data) {
        throw new Error("Invalid NumJS array: missing selection or data");
      }
      const dtype = typedArrayToDtype(aObject.selection.data);
      bObject = {
        _rtype: "ndarray",
        _rvalue: new Uint8Array(aObject.selection.data.buffer),
        _rshape: aObject.shape,
        _rdtype: dtype,
      };
    } else if (aObject instanceof Error) {
      console.error(aObject);
      bObject = {
        _rtype: "error",
        _rvalue: aObject.toString(),
        _rtrace: aObject.stack,
      };
    }
    // send objects supported by structure clone algorithm
    // https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Structured_clone_algorithm
    else if (
      aObject !== Object(aObject) ||
      aObject instanceof Boolean ||
      aObject instanceof String ||
      aObject instanceof Date ||
      aObject instanceof RegExp ||
      (typeof ImageData !== "undefined" && aObject instanceof ImageData) ||
      (typeof FileList !== "undefined" && aObject instanceof FileList) ||
      (typeof FileSystemDirectoryHandle !== "undefined" &&
        aObject instanceof FileSystemDirectoryHandle) ||
      (typeof FileSystemFileHandle !== "undefined" &&
        aObject instanceof FileSystemFileHandle) ||
      (typeof FileSystemHandle !== "undefined" &&
        aObject instanceof FileSystemHandle) ||
      (typeof FileSystemWritableFileStream !== "undefined" &&
        aObject instanceof FileSystemWritableFileStream)
    ) {
      bObject = aObject;
      // TODO: avoid object such as DynamicPlugin instance.
    } else if (aObject instanceof Blob) {
      let _current_pos = 0;
      async function read(length) {
        let blob;
        if (length) {
          blob = aObject.slice(_current_pos, _current_pos + length);
        } else {
          blob = aObject.slice(_current_pos);
        }
        const ret = new Uint8Array(await blob.arrayBuffer());
        _current_pos = _current_pos + ret.byteLength;
        return ret;
      }
      function seek(pos) {
        _current_pos = pos;
      }
      bObject = {
        _rtype: "iostream",
        _rnative: "js:blob",
        type: aObject.type,
        name: aObject.name,
        size: aObject.size,
        path: aObject._path || aObject.webkitRelativePath,
        read: await this._encode(read, session_id, local_workspace),
        seek: await this._encode(seek, session_id, local_workspace),
      };
    } else if (aObject instanceof ArrayBufferView) {
      const dtype = typedArrayToDtype(aObject);
      bObject = {
        _rtype: "typedarray",
        _rvalue: new Uint8Array(aObject.buffer),
        _rdtype: dtype,
      };
    } else if (aObject instanceof DataView) {
      bObject = {
        _rtype: "memoryview",
        _rvalue: new Uint8Array(aObject.buffer),
      };
    } else if (aObject instanceof Set) {
      bObject = {
        _rtype: "set",
        _rvalue: await this._encode(
          Array.from(aObject),
          session_id,
          local_workspace,
        ),
      };
    } else if (aObject instanceof Map) {
      bObject = {
        _rtype: "orderedmap",
        _rvalue: await this._encode(
          Array.from(aObject),
          session_id,
          local_workspace,
        ),
      };
    } else if (aObject.constructor === Object || Array.isArray(aObject)) {
      bObject = isarray ? [] : {};
      const keys = Object.keys(aObject);
      for (let k of keys) {
        bObject[k] = await this._encode(
          aObject[k],
          session_id,
          local_workspace,
        );
      }
    } else {
      throw `hypha-rpc: Unsupported data type: ${aObject}, you can register a custom codec to encode/decode the object.`;
    }

    if (!bObject) {
      throw new Error("Failed to encode object");
    }
    return bObject;
  }

  async decode(aObject) {
    return await this._decode(aObject);
  }

  async _decode(
    aObject,
    remote_parent,
    local_parent,
    remote_workspace,
    local_workspace,
  ) {
    if (!aObject) {
      return aObject;
    }
    let bObject;
    if (aObject._rtype) {
      if (
        this._codecs[aObject._rtype] &&
        this._codecs[aObject._rtype].decoder
      ) {
        const temp = aObject._rtype;
        delete aObject._rtype;
        aObject = await this._decode(
          aObject,
          remote_parent,
          local_parent,
          remote_workspace,
          local_workspace,
        );
        aObject._rtype = temp;

        bObject = await Promise.resolve(
          this._codecs[aObject._rtype].decoder(aObject),
        );
      } else if (aObject._rtype === "method") {
        bObject = this._generate_remote_method(
          aObject,
          remote_parent,
          local_parent,
          remote_workspace,
          local_workspace,
        );
      } else if (aObject._rtype === "generator") {
        // Create a method to fetch next items from the remote generator
        const gen_method = this._generate_remote_method(
          aObject,
          remote_parent,
          local_parent,
          remote_workspace,
          local_workspace,
        );

        // Create an async generator proxy
        async function* asyncGeneratorProxy() {
          while (true) {
            try {
              const next_item = await gen_method();
              // Check for StopIteration signal
              if (next_item && next_item._rtype === "stop_iteration") {
                break;
              }
              yield next_item;
            } catch (error) {
              console.error("Error in generator:", error);
              throw error;
            }
          }
        }
        bObject = asyncGeneratorProxy();
      } else if (aObject._rtype === "ndarray") {
        /*global nj tf*/
        //create build array/tensor if used in the plugin
        if (typeof nj !== "undefined" && nj.array) {
          if (Array.isArray(aObject._rvalue)) {
            aObject._rvalue = aObject._rvalue.reduce(_appendBuffer);
          }
          bObject = nj
            .array(new Uint8(aObject._rvalue), aObject._rdtype)
            .reshape(aObject._rshape);
        } else if (typeof tf !== "undefined" && tf.Tensor) {
          if (Array.isArray(aObject._rvalue)) {
            aObject._rvalue = aObject._rvalue.reduce(_appendBuffer);
          }
          const arraytype = dtypeToTypedArray[aObject._rdtype];
          bObject = tf.tensor(
            new arraytype(aObject._rvalue),
            aObject._rshape,
            aObject._rdtype,
          );
        } else {
          //keep it as regular if transfered to the main app
          bObject = aObject;
        }
      } else if (aObject._rtype === "error") {
        bObject = new Error(
          "RemoteError: " + aObject._rvalue + "\n" + (aObject._rtrace || ""),
        );
      } else if (aObject._rtype === "typedarray") {
        const arraytype = dtypeToTypedArray[aObject._rdtype];
        if (!arraytype)
          throw new Error("unsupported dtype: " + aObject._rdtype);
        const buffer = aObject._rvalue.buffer.slice(
          aObject._rvalue.byteOffset,
          aObject._rvalue.byteOffset + aObject._rvalue.byteLength,
        );
        bObject = new arraytype(buffer);
      } else if (aObject._rtype === "memoryview") {
        bObject = aObject._rvalue.buffer.slice(
          aObject._rvalue.byteOffset,
          aObject._rvalue.byteOffset + aObject._rvalue.byteLength,
        ); // ArrayBuffer
      } else if (aObject._rtype === "iostream") {
        if (aObject._rnative === "js:blob") {
          const read = await this._generate_remote_method(
            aObject.read,
            remote_parent,
            local_parent,
            remote_workspace,
            local_workspace,
          );
          const bytes = await read();
          bObject = new Blob([bytes], {
            type: aObject.type,
            name: aObject.name,
          });
        } else {
          bObject = {};
          for (let k of Object.keys(aObject)) {
            if (!k.startsWith("_")) {
              bObject[k] = await this._decode(
                aObject[k],
                remote_parent,
                local_parent,
                remote_workspace,
                local_workspace,
              );
            }
          }
        }
        bObject["__rpc_object__"] = aObject;
      } else if (aObject._rtype === "orderedmap") {
        bObject = new Map(
          await this._decode(
            aObject._rvalue,
            remote_parent,
            local_parent,
            remote_workspace,
            local_workspace,
          ),
        );
      } else if (aObject._rtype === "set") {
        bObject = new Set(
          await this._decode(
            aObject._rvalue,
            remote_parent,
            local_parent,
            remote_workspace,
            local_workspace,
          ),
        );
      } else {
        const temp = aObject._rtype;
        delete aObject._rtype;
        bObject = await this._decode(
          aObject,
          remote_parent,
          local_parent,
          remote_workspace,
          local_workspace,
        );
        bObject._rtype = temp;
      }
    } else if (aObject.constructor === Object || Array.isArray(aObject)) {
      const isarray = Array.isArray(aObject);
      bObject = isarray ? [] : {};
      for (let k of Object.keys(aObject)) {
        if (isarray || aObject.hasOwnProperty(k)) {
          const v = aObject[k];
          bObject[k] = await this._decode(
            v,
            remote_parent,
            local_parent,
            remote_workspace,
            local_workspace,
          );
        }
      }
    } else {
      bObject = aObject;
    }
    if (bObject === undefined) {
      throw new Error("Failed to decode object");
    }
    return bObject;
  }

  _expand_promise(data) {
    return {
      heartbeat: {
        _rtype: "method",
        _rtarget: data.from.split("/")[1],
        _rmethod: data.session + ".heartbeat",
        _rdoc: `heartbeat callback for method: ${data.method}`,
      },
      resolve: {
        _rtype: "method",
        _rtarget: data.from.split("/")[1],
        _rmethod: data.session + ".resolve",
        _rdoc: `resolve callback for method: ${data.method}`,
      },
      reject: {
        _rtype: "method",
        _rtarget: data.from.split("/")[1],
        _rmethod: data.session + ".reject",
        _rdoc: `reject callback for method: ${data.method}`,
      },
      interval: data.t,
    };
  }
}
