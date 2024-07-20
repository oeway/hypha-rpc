const path = require('path');

module.exports = {
  mode: process.env.NODE_ENV || 'development',
  entry: {
      'hyphaWebsocketClient': path.resolve(__dirname, 'src', 'websocket-client.js'),
  },
  output: {
      globalObject: 'this',
      path: path.resolve(__dirname, 'dist'),
      filename: (pathData) => {
        const outputNames = {
          "hyphaWebsocketClient": "hypha-rpc-websocket",
        };
        const name = outputNames[pathData.chunk.name];
        return process.env.NODE_ENV === 'production' ? name + '.min.js' : name + '.js';
      },
      library: '[name]',
      libraryTarget: 'umd',
      umdNamedDefine: true
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
