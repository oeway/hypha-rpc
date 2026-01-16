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
 * Wait for the S3 service to be available via WebSocket
 * @param {string} host - The host to check (default: 127.0.0.1)
 * @param {number} port - The port to check (default: 9394)
 * @param {number} timeout - Total timeout in seconds (default: 60)
 * @param {number} interval - Check interval in seconds (default: 2)
 * @returns {Promise<void>}
 */
async function waitForS3Service(host = '127.0.0.1', port = 9394, timeout = 60, interval = 2) {
  const startTime = Date.now();
  const timeoutMs = timeout * 1000;
  const intervalMs = interval * 1000;
  
  console.log(`⏳ Waiting for S3 service to be available...`);

  while (Date.now() - startTime < timeoutMs) {
    try {
      const available = await checkS3Service(host, port);
      if (available) {
        console.log(`✅ S3 service is ready! (took ${((Date.now() - startTime) / 1000).toFixed(1)}s)`);
        return true;
      }
    } catch (error) {
      console.log(`⚠️ S3 check failed: ${error.message}`);
    }
    
    await sleep(intervalMs);
  }
  
  throw new Error(`❌ Timeout: S3 service did not become ready within ${timeout} seconds`);
}

/**
 * Check if S3 service is available via HTTP endpoint check (safer than RPC)
 * @param {string} host
 * @param {number} port
 * @returns {Promise<boolean>}
 */
async function checkS3Service(host, port) {
  try {
    // First check if MinIO is responding on its port
    const minioHealthy = await checkMinioHealth(host, 9007);
    if (!minioHealthy) {
      console.log(`⚠️ MinIO server not responding on port 9007`);
      return false;
    }
    
    // Then check if Hypha server has S3 service registered
    const s3ServiceAvailable = await checkHyphaS3Service(host, port);
    return s3ServiceAvailable;
  } catch (error) {
    console.log(`⚠️ S3 service check failed: ${error.message}`);
    return false;
  }
}

/**
 * Check if MinIO server is responding
 * @param {string} host
 * @param {number} minioPort
 * @returns {Promise<boolean>}
 */
function checkMinioHealth(host, minioPort) {
  return new Promise((resolve) => {
    const req = http.get(`http://${host}:${minioPort}/minio/health/live`, (res) => {
      resolve(res.statusCode === 200);
    });

    req.on('error', () => {
      resolve(false);
    });

    req.setTimeout(3000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

/**
 * Check if Hypha server has S3 service available via services endpoint
 * @param {string} host
 * @param {number} port
 * @returns {Promise<boolean>}
 */
function checkHyphaS3Service(host, port) {
  return new Promise((resolve) => {
    const req = http.get(`http://${host}:${port}/public/services`, (res) => {
      if (res.statusCode !== 200) {
        resolve(false);
        return;
      }
      
      let data = '';
      res.on('data', (chunk) => {
        data += chunk;
      });
      
      res.on('end', () => {
        try {
          const services = JSON.parse(data);
          // Check if S3 storage service is available
          const hasS3Service = services.some(service => 
            service.id && service.id.includes('s3-storage')
          );
          resolve(hasS3Service);
        } catch (error) {
          resolve(false);
        }
      });
    });

    req.on('error', () => {
      resolve(false);
    });

    req.setTimeout(5000, () => {
      req.destroy();
      resolve(false);
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
    .then(async () => {
      console.log('🚀 Server is ready!');
      
      // Try to check S3 availability but don't fail if it's not ready
      try {
        console.log('⚠️ Checking S3 service availability (this may take a moment)...');
        const s3Ready = await waitForS3Service(host, port, 30); // Reduced timeout
        
        if (s3Ready) {
          console.log('✅ S3 service is also ready!');
        } else {
          console.log('⚠️ S3 service not ready yet, but proceeding with tests (some tests may fail)');
        }
      } catch (error) {
        console.log('⚠️ S3 service check failed, but proceeding with tests (some tests may fail)');
        console.log(`   Error details: ${error.message}`);
      }
      
      console.log('🚀 Tests can now run!');
      process.exit(0);
    })
    .catch((error) => {
      console.error(error.message);
      process.exit(1);
    });
}

module.exports = { waitForServer, waitForS3Service }; 