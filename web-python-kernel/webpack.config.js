const path = require('path');
const { CleanWebpackPlugin } = require('clean-webpack-plugin');
const CopyWebpackPlugin = require('copy-webpack-plugin');
const webpack = require('webpack');

const isESM = process.env.BUILD_ESM === 'true';

const config = {
  mode: process.env.NODE_ENV || 'development',
  entry: {
    'web-python-kernel': path.resolve(__dirname, 'src', 'index.ts'),
  },
  devtool: 'source-map',
  devServer: {
    static: {
      directory: path.resolve(__dirname, 'dist'),
    },
    port: 9090,
    hot: true,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
      "Access-Control-Allow-Headers": "X-Requested-With, content-type, Authorization"
    }
  },
  externals: {
    'node:fs': '{}',
    'node:fs/promises': '{}',
    'node:path': '{}',
    'node:crypto': '{}',
    'node:url': '{}',
    'node:vm': '{}',
    'node:child_process': '{}'
  },
  plugins: [
    new CleanWebpackPlugin(),
    new webpack.ProvidePlugin({
      process: 'process/browser',
    }),
    new CopyWebpackPlugin({
      patterns: [
        {
          from: 'src/pypi',
          to: 'pypi',
          noErrorOnMissing: true
        },
        {
          from: 'src/schema',
          to: 'schema',
          noErrorOnMissing: true
        },

      ]
    })
  ],
  module: {
    rules: [
      // Handle worker files first
      {
        test: /\.worker\.ts$/,
        use: {
          loader: 'worker-loader',
          options: {
            filename: '[name].[contenthash].worker.js',
            inline: 'no-fallback'
          }
        },
      },
      {
        test: /\.ts$/,
        use: 'ts-loader',
        exclude: /node_modules/,
      },
      {
        test: /\.css$/,
        use: ['style-loader', 'css-loader'],
      },
      {
        test: /\.py$/,
        type: 'asset/resource',
      },
      {
        test: /\.whl$/,
        type: 'asset/resource',
      },
      {
        test: /\.json$/,
        type: 'asset/resource',
      },
    ],
  },
  resolve: {
    extensions: ['.ts', '.js'],
    alias: {
      // Create alias for worker to resolve the import issue
      './worker.worker': path.resolve(__dirname, 'src/worker.worker.ts'),
    },
    fallback: {
      "fs": false,
      "path": false,
      "crypto": false,
      "url": false,
      "util": false,
      "stream": false,
      "buffer": false,
      "process": require.resolve("process/browser"),
      "http": false,
      "https": false,
      "assert": false,
      "tty": false,
      "os": false
    }
  },
  ignoreWarnings: [
    {
      module: /node_modules/,
      message: /Failed to parse source map/,
    },
    {
      module: /node_modules\/pyodide/,
      message: /Critical dependency: the request of a dependency is an expression/,
    },
  ],
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: process.env.NODE_ENV === 'production'
      ? (isESM ? 'web-python-kernel.min.mjs' : 'web-python-kernel.min.js')
      : (isESM ? 'web-python-kernel.mjs' : 'web-python-kernel.js'),
    globalObject: 'this',
    ...(isESM ? {
      module: true,
      libraryTarget: 'module',
    } : {
      library: 'WebPythonKernel',
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