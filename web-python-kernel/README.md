# 🐍 Web Python Kernel

A web-based Python kernel for browser environments with TypeScript support.

## 🚀 Quick Start - Playground

### **Option 1: Simple Setup (Recommended)**
```bash
npm run playground
```
This will build the kernel and start the playground at http://localhost:8080/playground.html

### **Option 2: Manual Steps**
```bash
# Build the kernel bundle
npm run build

# Start the development server  
npm run serve

# Open http://localhost:8080/playground.html
```

---

## 🎮 Playground Features

The playground offers **two modes**:

### **Simple Mode (CDN Pyodide)**
- ✅ **No build required** - uses Pyodide directly from CDN
- ✅ **Fast startup** - loads in ~30 seconds
- ✅ **Basic packages** - numpy, matplotlib available
- ✅ **Perfect for learning** and simple scripts

### **Advanced Mode (Web-Python-Kernel)**  
- ✅ **Full kernel features** - uses your web-python-kernel implementation
- ✅ **Event streaming** - KERNEL_BUSY, KERNEL_IDLE, STREAM events
- ✅ **Advanced packages** - pyodide_kernel, piplite, ipykernel
- ✅ **Perfect for development** and testing kernel features

**The playground automatically falls back to Simple Mode if the kernel bundle is not available.**

---

## 🛠 Development Commands

| Command | Description |
|---------|-------------|
| `npm run playground` | Build and start playground |
| `npm run build` | Build kernel bundle |
| `npm run serve` | Start development server |
| `npm run test` | Run all tests (33 tests) |
| `npm run test:watch` | Run tests in watch mode |
| `npm run clean` | Clean build artifacts |

---

## 🧪 Testing

```bash
# Run all tests
npm test

# Expected output: 33/33 tests passing
# Tests cover: kernel functionality, manager features, streaming
```

---

## 📁 Project Structure

```
web-python-kernel/
├── src/                 # TypeScript source code
│   ├── manager.ts       # Kernel manager 
│   ├── index.ts         # Main kernel implementation
│   ├── pypi/           # Python wheel files
│   └── ...
├── tests/              # Test files
│   ├── kernel_test.ts
│   ├── kernel_manager_test.ts  
│   └── kernel_stream_test.ts
├── playground.html     # Interactive playground
├── serve.js           # Development server
└── package.json       # Dependencies and scripts
```

---

## 🎯 Key Features

### **Real Python Execution**
- ✅ **Pyodide integration** - real Python 3.11 in browser
- ✅ **Package support** - numpy, matplotlib, custom wheels  
- ✅ **Import system** - standard Python imports work
- ✅ **Error handling** - proper Python exception handling

### **Kernel Management**
- ✅ **Multiple kernels** - create and manage multiple Python environments
- ✅ **Event system** - KERNEL_BUSY, KERNEL_IDLE, STREAM, ERROR events
- ✅ **Stream capture** - capture stdout, stderr, results separately
- ✅ **Lifecycle management** - proper kernel creation and destruction

### **Development Tools**
- ✅ **TypeScript** - full type safety and intellisense
- ✅ **Testing suite** - comprehensive test coverage
- ✅ **Hot reload** - fast development iteration
- ✅ **Interactive playground** - test features immediately

---

## 🎨 Playground Usage

1. **Open** http://localhost:8080/playground.html
2. **Select Mode**: Simple (CDN) or Advanced (Kernel)
3. **Initialize**: Click the Initialize button  
4. **Code**: Write Python code in the editor
5. **Run**: Click "▶ Run Code" or press Ctrl+Enter
6. **Experiment**: Try the example code snippets

### **Example Code**
```python
# Basic Python
print("Hello, World!")

# Data processing  
numbers = [1, 2, 3, 4, 5]
squared = [x**2 for x in numbers]
print(f"Squared: {squared}")

# Math operations
import math
print(f"π = {math.pi:.4f}")

# NumPy (if available)
import numpy as np
arr = np.array([1, 2, 3])
print(f"NumPy array: {arr}")
```

---

## 🚀 Ready to Go!

Your web-python-kernel playground is ready! 

**Quick start**: Run `npm run playground` and open the URL in your browser.

Happy coding! 🐍✨ 