#!/usr/bin/env node

const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = process.env.PORT || 8080;
// set dist dir
const ROOT_DIR = __dirname;

const MIME_TYPES = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.mjs': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.ts': 'text/typescript',
  '.whl': 'application/octet-stream'
};

// Simple file watching for development
const watchedFiles = new Set();
const clients = new Set();

function watchFile(filepath) {
  if (watchedFiles.has(filepath)) return;
  
  watchedFiles.add(filepath);
  fs.watchFile(filepath, (curr, prev) => {
    if (curr.mtime > prev.mtime) {
      console.log(`📝 File changed: ${path.relative(ROOT_DIR, filepath)}`);
      // Notify all connected clients about file change
      clients.forEach(client => {
        if (client.readyState === 1) { // WebSocket OPEN
          client.send(JSON.stringify({ type: 'reload', file: filepath }));
        }
      });
    }
  });
}

const server = http.createServer((req, res) => {
  // Parse URL
  const parsedUrl = url.parse(req.url);
  let pathname = parsedUrl.pathname;
  
  // Security: prevent path traversal
  pathname = pathname.replace(/\.\./g, '');
  
  if (req.url === '/health-check') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('OK');
    return
  }

  // Default to playground.html for root
  if (pathname === '/') {
    pathname = '/playground.html';
  }

  
  
  const filepath = path.join(ROOT_DIR, pathname);
  
  // Check if file exists
  fs.access(filepath, fs.constants.F_OK, (err) => {
    if (err) {
      // File not found
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('404 Not Found');
      return;
    }
    
    // Watch HTML, JS, CSS, MJS files for changes
    const ext = path.extname(filepath).toLowerCase();
    if (['.html', '.js', '.css', '.mjs'].includes(ext)) {
      watchFile(filepath);
    }
    
    // Get MIME type
    const mimeType = MIME_TYPES[ext] || 'application/octet-stream';
    
    // Set CORS headers for development
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
    
    // Handle OPTIONS preflight
    if (req.method === 'OPTIONS') {
      res.writeHead(200);
      res.end();
      return;
    }
    
    // Read and serve file
    fs.readFile(filepath, (err, data) => {
      if (err) {
        res.writeHead(500, { 'Content-Type': 'text/plain' });
        res.end('500 Internal Server Error');
        return;
      }
      
      // Inject auto-reload script into HTML files
      if (ext === '.html') {
        const htmlString = data.toString();
        const autoReloadScript = `
<script>
  // Auto-reload for development
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    const checkForUpdates = () => {
      fetch('/health-check')
        .then(response => response.ok ? response.text() : Promise.reject())
        .then(() => {
          // Server is responsive, check again in 1 second
          setTimeout(checkForUpdates, 1000);
        })
        .catch(() => {
          // Server might be restarting, reload page
          window.location.reload();
        });
    };
    setTimeout(checkForUpdates, 1000);
  }
</script>
</head>`;
        const modifiedHtml = htmlString.replace('</head>', autoReloadScript);
        res.writeHead(200, { 'Content-Type': mimeType });
        res.end(modifiedHtml);
      } else {
        res.writeHead(200, { 'Content-Type': mimeType });
        res.end(data);
      }
    });
  });
});

server.listen(PORT, () => {
  console.log(`🚀 Development server running at:`);
  console.log(`   Local:   http://localhost:${PORT}/`);
  console.log(`   Playground: http://localhost:${PORT}/playground.html`);
  console.log('');
  console.log('📝 Features:');
  console.log('   - Full web-python-kernel with visualization support');
  console.log('   - Matplotlib, Plotly, Seaborn plotting capabilities');
  console.log('   - Automatic display_data rendering for plots');
  console.log('   - File watching with auto-reload on changes');
  console.log('');
  console.log('📁 Watching for changes in HTML, JS, CSS files...');
  console.log('Press Ctrl+C to stop the server');
});

// Graceful shutdown
process.on('SIGINT', () => {
  console.log('\n👋 Shutting down development server...');
  
  // Clean up file watchers
  watchedFiles.forEach(filepath => {
    fs.unwatchFile(filepath);
  });
  
  server.close(() => {
    console.log('Server stopped.');
    process.exit(0);
  });
}); 