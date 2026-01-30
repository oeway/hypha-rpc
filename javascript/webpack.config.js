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
