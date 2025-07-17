# Development Guide

## Quick Start

```bash
# Install dependencies
npm install

# Start development server
npm start
# or
npm run dev
```

## Available Scripts

### Development Scripts

- **`npm start`** - Starts the development server (alias for `npm run dev`)
- **`npm run dev`** - Build in development mode and start file-watching server
- **`npm run dev:hot`** - Start webpack dev server with hot module replacement
- **`npm run playground`** - Start the playground in development mode

### Build Scripts

- **`npm run build`** - Create production build (clean → build:prod)
- **`npm run build:dev`** - Create development build with source maps
- **`npm run build:prod`** - Create optimized production build
- **`npm run rebuild`** - Clean and rebuild in development mode

### Testing Scripts

- **`npm test`** - Run all tests once (headless Chrome)
- **`npm run test:watch`** - Run tests in watch mode
- **`npm run test:chrome`** - Run tests in Chrome browser

### Code Quality Scripts

- **`npm run lint`** - Check code style and errors
- **`npm run lint:fix`** - Fix linting issues automatically
- **`npm run format`** - Format code with Prettier
- **`npm run format:check`** - Check if code is formatted correctly

### Publishing Scripts

- **`npm run publish-npm`** - Full publishing workflow (lint → format → test → build → publish)
- **`npm run prepublishOnly`** - Automatic pre-publish checks (runs before npm publish)

### Utility Scripts

- **`npm run clean`** - Remove build artifacts and cache

## Development Workflow

### For Development

1. **Standard Development** (with file watching and auto-reload):
   ```bash
   npm run dev
   ```
   - Builds in development mode
   - Starts file-watching server on http://localhost:8080
   - Auto-reloads browser on file changes
   - Watches HTML, JS, CSS files

2. **Hot Module Replacement** (advanced):
   ```bash
   npm run dev:hot
   ```
   - Uses webpack-dev-server with HMR
   - Faster rebuilds and hot reloading
   - Opens browser automatically

### For Production

1. **Build for Production**:
   ```bash
   npm run build
   ```
   - Creates optimized, minified bundle
   - Generates source maps
   - Copies necessary assets

2. **Publishing to NPM**:
   ```bash
   npm run publish-npm
   ```
   - Runs all quality checks
   - Creates production build
   - Publishes to npm registry

## Development Server Features

- **Auto-reload**: Browser refreshes on file changes
- **CORS enabled**: For cross-origin requests
- **File watching**: Monitors HTML, JS, CSS files
- **Health checks**: Monitors server status
- **Static file serving**: Serves all project files

## Build Outputs

- **Development**: `web-python-kernel.js` (with source maps)
- **Production**: `web-python-kernel.min.js` (optimized)
- **Assets**: `pypi/` directory copied to build

## Browser Support

- Modern browsers with ES6+ support
- WebAssembly support required for Pyodide
- LocalStorage for kernel state persistence

## Performance Tips

- Use `npm run dev:hot` for fastest development iteration
- Production builds are optimized for size and performance
- Source maps available in development for debugging 