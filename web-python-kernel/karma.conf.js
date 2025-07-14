// Clean Karma configuration for all tests
const path = require('path');

const testWebpackConfig = {
  mode: 'development',
  devtool: 'inline-source-map',
  resolve: {
    extensions: ['.ts', '.js'],
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
  module: {
    rules: [
      {
        test: /\.ts$/,
        use: 'ts-loader',
        exclude: /node_modules/,
      }
    ],
  },
  plugins: [
    new (require('webpack')).ProvidePlugin({
      process: 'process/browser',
    })
  ],
  // Optimize for memory usage
  optimization: {
    minimize: false,
    splitChunks: false
  }
};

module.exports = function (config) {
    config.set({
        // base path that will be used to resolve all patterns (eg. files, exclude)
        basePath: '',

        // frameworks to use
        frameworks: ['mocha'],

        // list of files / patterns to load in the browser
        files: [
            'tests/kernel_test.ts',
            'tests/kernel_manager_test.ts',
            'tests/kernel_stream_test.ts',
            // Static wheel files and schema - served but not included in browser
            { pattern: 'src/pypi/**/*.whl', watched: false, included: false, served: true },
            { pattern: 'src/pypi/**/*.json', watched: false, included: false, served: true },
            { pattern: 'src/schema/**/*.json', watched: false, included: false, served: true }
        ],

        // Map URLs to serve static files from the correct locations
        proxies: {
            '/pypi/': '/base/src/pypi/',
            '/schema/': '/base/src/schema/'
        },

        // list of files / patterns to exclude
        exclude: [],

        // preprocess matching files before serving them to the browser
        preprocessors: {
            'tests/kernel_test.ts': ['webpack', 'sourcemap'],
            'tests/kernel_manager_test.ts': ['webpack', 'sourcemap'],
            'tests/kernel_stream_test.ts': ['webpack', 'sourcemap']
        },

        webpack: testWebpackConfig,

        webpackMiddleware: {
            stats: 'errors-only',
        },

        // test results reporter to use
        reporters: ["spec"],
        specReporter: {
            maxLogLines: 5,
            suppressErrorSummary: true,
            suppressFailed: false,
            suppressPassed: false,
            suppressSkipped: true,
            showSpecTiming: false
        },

        // web server port
        port: 9876,

        // enable / disable colors in the output (reporters and logs)
        colors: true,

        // level of logging
        logLevel: config.LOG_INFO,

        // enable / disable watching file and executing tests whenever any file changes
        autoWatch: false,

        // start these browsers
        browsers: ['ChromeHeadless'],

        // Continuous Integration mode
        singleRun: true,

        // Concurrency level
        concurrency: Infinity,

        // Timeout settings
        captureTimeout: 60000,
        browserDisconnectTolerance: 3,
        browserDisconnectTimeout: 10000,
        browserNoActivityTimeout: 60000
    });
}; 