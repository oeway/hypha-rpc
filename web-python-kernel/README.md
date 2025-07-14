# Web Python Kernel

A Python kernel based on Pyodide for browser environments. This package provides a complete Jupyter kernel implementation that can run Python code directly in the browser using Pyodide.

## Features

- **Python Kernel**: Full Python kernel implementation using Pyodide
- **Browser Support**: Runs entirely in the browser (no server required)
- **Worker Support**: Supports both main thread and web worker execution modes
- **Event System**: Complete event system for kernel communication
- **Streaming Execution**: Support for streaming code execution with real-time output
- **Interrupt Support**: Kernel interruption capabilities (worker mode only)
- **TypeScript**: Full TypeScript support with type definitions
- **Multiple Formats**: Available as both ESM and UMD modules

## Installation

```bash
npm install web-python-kernel
```

## Usage

### Basic Usage

```javascript
import { KernelManager, KernelMode, KernelLanguage } from 'web-python-kernel';

// Create a kernel manager
const manager = new KernelManager();

// Create a Python kernel
const kernelId = await manager.createKernel({
  mode: KernelMode.WORKER, // or KernelMode.MAIN_THREAD
  lang: KernelLanguage.PYTHON
});

// Execute Python code
const result = await manager.execute(kernelId, 'print("Hello, World!")');
console.log(result); // { success: true, result: ... }
```

### Event Handling

```javascript
import { KernelEvents } from 'web-python-kernel';

// Listen for output events
manager.onKernelEvent(kernelId, KernelEvents.STREAM, (data) => {
  console.log(`${data.name}: ${data.text}`);
});

// Listen for execution results
manager.onKernelEvent(kernelId, KernelEvents.EXECUTE_RESULT, (data) => {
  console.log('Execution result:', data.data);
});

// Listen for errors
manager.onKernelEvent(kernelId, KernelEvents.EXECUTE_ERROR, (data) => {
  console.error(`${data.ename}: ${data.evalue}`);
});
```

### Streaming Execution

```javascript
// Stream execution with real-time output
const streamGenerator = manager.executeStream(kernelId, `
for i in range(5):
    print(f"Count: {i}")
    import time
    time.sleep(1)
`);

for await (const event of streamGenerator) {
  if (event.type === 'stream') {
    console.log(event.data.text);
  }
}
```

### Kernel Management

```javascript
// List all kernels
const kernels = await manager.listKernels();

// Get kernel information
const kernel = manager.getKernel(kernelId);

// Destroy a kernel
await manager.destroyKernel(kernelId);

// Destroy all kernels
await manager.destroyAll();
```

## API Reference

### KernelManager

The main class for managing kernel instances.

#### Methods

- `createKernel(options)`: Create a new kernel instance
- `getKernel(id)`: Get a kernel instance by ID
- `listKernels()`: List all kernel instances
- `execute(kernelId, code)`: Execute code in a kernel
- `executeStream(kernelId, code)`: Execute code with streaming output
- `destroyKernel(id)`: Destroy a kernel instance
- `destroyAll()`: Destroy all kernel instances

### Events

- `KernelEvents.STREAM`: Output stream events (stdout/stderr)
- `KernelEvents.EXECUTE_RESULT`: Execution result events
- `KernelEvents.EXECUTE_ERROR`: Execution error events
- `KernelEvents.DISPLAY_DATA`: Display data events
- `KernelEvents.INPUT_REQUEST`: Input request events

### Kernel Modes

- `KernelMode.MAIN_THREAD`: Run kernel in main thread
- `KernelMode.WORKER`: Run kernel in web worker (recommended)

## Development

### Setup

```bash
# Install dependencies
npm install

# Build the project
npm run build

# Run tests
npm test

# Watch mode for development
npm run watch
```

### Building

The project builds both ESM and UMD formats:

```bash
npm run build
```

This creates:
- `dist/web-python-kernel.js` - UMD format
- `dist/web-python-kernel.mjs` - ESM format
- `dist/web-python-kernel.min.js` - Minified UMD
- `dist/web-python-kernel.min.mjs` - Minified ESM

### Testing

```bash
# Run all tests
npm test

# Run tests in watch mode
npm run test:watch

# Run tests for CI
npm run test:ci
```

## Browser Compatibility

- Chrome/Chromium 67+
- Firefox 79+
- Safari 14+
- Edge 79+

Note: SharedArrayBuffer support is required for kernel interruption features.

## License

MIT License - see LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Run the test suite
6. Submit a pull request

## Support

For issues and questions, please use the GitHub issue tracker. 