#!/usr/bin/env node

const http = require('http');

/**
 * Wait for the hypha server to be ready by polling the health endpoint
 * @param {string} host - The host to check (default: 127.0.0.1)
 * @param {number} port - The port to check (default: 9394)
 * @param {number} timeout - Total timeout in seconds (default: 30)
 * @param {number} interval - Check interval in seconds (default: 0.5)
 * @returns {Promise<void>}
 */
async function waitForServer(host = '127.0.0.1', port = 9394, timeout = 30, interval = 0.5) {
  const startTime = Date.now();
  const timeoutMs = timeout * 1000;
  const intervalMs = interval * 1000;
  
  console.log(`⏳ Waiting for hypha server at http://${host}:${port}/health/readiness...`);

  while (Date.now() - startTime < timeoutMs) {
    try {
      await checkHealth(host, port);
      console.log(`✅ Hypha server is ready! (took ${((Date.now() - startTime) / 1000).toFixed(1)}s)`);
      return;
    } catch (error) {
      // Server not ready yet, continue waiting
      await sleep(intervalMs);
    }
  }
  
  throw new Error(`❌ Timeout: Hypha server did not become ready within ${timeout} seconds`);
}

/**
 * Check if the server is ready by making a request to the health endpoint
 * @param {string} host
 * @param {number} port
 * @returns {Promise<void>}
 */
function checkHealth(host, port) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://${host}:${port}/health/readiness`, (res) => {
      if (res.statusCode === 200) {
        resolve();
      } else {
        reject(new Error(`Health check failed with status: ${res.statusCode}`));
      }
    });

    req.on('error', (error) => {
      reject(error);
    });

    req.setTimeout(5000, () => {
      req.destroy();
      reject(new Error('Health check request timeout'));
    });
  });
}

/**
 * Sleep for the specified number of milliseconds
 * @param {number} ms
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Parse command line arguments
const args = process.argv.slice(2);
const host = args[0] || '127.0.0.1';
const port = parseInt(args[1]) || 9394;
const timeout = parseInt(args[2]) || 30;

// Main execution
if (require.main === module) {
  waitForServer(host, port, timeout)
    .then(() => {
      console.log('🚀 Server is ready, you can now run your tests!');
      process.exit(0);
    })
    .catch((error) => {
      console.error(error.message);
      process.exit(1);
    });
}

module.exports = { waitForServer }; 