/** Tiny static server so the ES modules in web/ and src/ load over http. */

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 8080);

const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};

http
  .createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === '/') {
      // Redirect rather than serve here, so the page's relative paths resolve.
      res.writeHead(302, { Location: '/web/index.html' });
      res.end();
      return;
    }
    if (pathname.endsWith('/')) pathname += 'index.html';

    const filePath = path.join(root, path.normalize(pathname).replace(/^(\.\.[/\\])+/, ''));
    if (!filePath.startsWith(root) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not found');
      return;
    }

    res.writeHead(200, { 'Content-Type': types[path.extname(filePath)] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  })
  .listen(port, () => {
    console.log(`Badminton scheduler running at http://localhost:${port}`);
  });
