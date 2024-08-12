const path = require('path');

const commonConfig = {
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
};

const umdConfig = {
  ...commonConfig,
  output: {
      path: path.resolve(__dirname, 'dist'),
      filename: process.env.NODE_ENV === 'production' ? 'hypha-rpc-websocket.min.js' : 'hypha-rpc-websocket.js',
      globalObject: 'this',
      library: 'hyphaWebsocketClient',
      libraryTarget: 'umd',
      umdNamedDefine: true,
  },
};

const esmConfig = {
  ...commonConfig,
  output: {
      path: path.resolve(__dirname, 'dist'),
      filename: process.env.NODE_ENV === 'production' ? 'hypha-rpc-websocket.esm.min.js' : 'hypha-rpc-websocket.esm.js',
      module: true,
      libraryTarget: 'module',
  },
  experiments: {
    outputModule: true, // Required for module output
  },
};

module.exports = [umdConfig, esmConfig];
