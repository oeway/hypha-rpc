const path = require('path');
const webpack = require('webpack');
const CopyWebpackPlugin = require('copy-webpack-plugin');
const { CleanWebpackPlugin } = require('clean-webpack-plugin');

module.exports = (env, argv) => {
  const isProduction = argv.mode === 'production';
  
  return {
    entry: './src/index.ts',
    mode: argv.mode || 'development',
    
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          use: {
            loader: 'ts-loader',
            options: {
              transpileOnly: true,
              compilerOptions: {
                noEmit: false,
              },
            },
          },
          exclude: /node_modules/,
        },
        {
          test: /\.css$/,
          use: ['style-loader', 'css-loader'],
        },
        {
          test: /\.worker\.ts$/,
          loader: 'ts-loader',
        },
      ],
    },
    
    resolve: {
      extensions: ['.tsx', '.ts', '.js'],
      fallback: {
        "fs": false,
        "path": require.resolve("path-browserify"),
        "crypto": require.resolve("crypto-browserify"),
        "stream": require.resolve("stream-browserify"),
        "buffer": require.resolve("buffer"),
        "process": require.resolve("process/browser"),
        "vm": false,
        "os": false
      }
    },
    
    output: {
      filename: isProduction ? 'web-python-kernel.min.js' : 'web-python-kernel.js',
      path: path.resolve(__dirname, 'dist'),
      library: {
        name: 'WebPythonKernel',
        type: 'umd',
      },
      clean: true,
    },
    
    plugins: [
      new CleanWebpackPlugin(),
      new webpack.ProvidePlugin({
        process: 'process/browser',
        Buffer: ['buffer', 'Buffer'],
      }),

      new CopyWebpackPlugin({
        patterns: [
          {
            from: 'src/pypi',
            to: 'pypi',
          },
        ],
      }),
    ],
    
    devtool: isProduction ? 'source-map' : 'eval-source-map',
    
    optimization: {
      minimize: isProduction,
      usedExports: true,
      sideEffects: false,
    },
    
    // Development server configuration
    devServer: {
      static: {
        directory: path.join(__dirname, '.'),
      },
      port: 8080,
      hot: true,
      open: true,
      openPage: 'playground.html',
      compress: true,
      historyApiFallback: {
        index: '/playground.html'
      },
      client: {
        overlay: {
          errors: true,
          warnings: false,
        },
      },
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, PATCH, OPTIONS',
        'Access-Control-Allow-Headers': 'X-Requested-With, content-type, Authorization',
      },
    },
    
    externals: {
      'node-fetch': 'fetch',
    },
    
    stats: {
      errorDetails: true,
    },
    
    performance: {
      hints: isProduction ? 'warning' : false,
      maxEntrypointSize: 2000000,
      maxAssetSize: 2000000,
    },
  };
}; 