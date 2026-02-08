const path = require('path');

const isESM = process.env.BUILD_ESM === 'true';

const config = {
  mode: process.env.NODE_ENV || 'development',
  entry: {
    'hyphaWebsocketClient': path.resolve(__dirname, 'src', 'websocket-client.js'),
  },
  devtool: 'source-map',
  devServer: {
    static: {
      directory: path.resolve(__dirname, 'dist'),
    },
    port: 9099,
    hot: true,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
      "Access-Control-Allow-Headers": "X-Requested-With, content-type, Authorization"
    }
  },
  plugins: [],
  module: {
    rules: [
      {
        test: /\.css$/,
        use: ['style-loader', 'css-loader'],
      },
    ],
  },
  // Disable code-splitting so every build is a single self-contained file.
  // Without this, webpack can create dynamic chunks (e.g. for @msgpack/msgpack)
  // that break when consumer apps re-bundle hypha-rpc with their own bundler
  // (the inner webpack runtime resolves chunk URLs relative to the page, not
  // relative to node_modules/hypha-rpc/dist/).
  optimization: {
    splitChunks: false,
  },
  ignoreWarnings: [
    {
      module: /node_modules/,
      message: /Failed to parse source map/,
    },
  ],
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: process.env.NODE_ENV === 'production'
      ? (isESM ? 'hypha-rpc-websocket.min.mjs' : 'hypha-rpc-websocket.min.js')
      : (isESM ? 'hypha-rpc-websocket.mjs' : 'hypha-rpc-websocket.js'),
    globalObject: 'this',
    ...(isESM ? {
      module: true,
      libraryTarget: 'module',
    } : {
      library: 'hyphaWebsocketClient',
      libraryTarget: 'umd',
      umdNamedDefine: true,
    }),
  },
  ...(isESM && {
    experiments: {
      outputModule: true,
    },
  }),
};

module.exports = config;
