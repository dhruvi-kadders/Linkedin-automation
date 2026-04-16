// backend/src/server.js
// Express app entry point

const express = require('express');
const cors    = require('cors');
const path    = require('path');
require('dotenv').config({ path: path.join(__dirname, '../config/.env') });

const routes = require('./api/routes');

const app  = express();
const PORT = process.env.PORT || 3000;

// ── Middleware ────────────────────────────────────────────────────────────────
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Serve static frontend (optional)
app.use(express.static(path.join(__dirname, '../../frontend')));

// Serve uploaded resumes (protected in production)
app.use('/uploads', express.static(path.join(__dirname, '../uploads')));

// ── API Routes ────────────────────────────────────────────────────────────────
app.use('/api', routes);

// ── Error handler ─────────────────────────────────────────────────────────────
app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(500).json({ error: err.message || 'Internal server error' });
});

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`
╔══════════════════════════════════════╗
║   LinkedIn Bot API running           ║
║   http://localhost:${PORT}             ║
║   Frontend: open frontend/index.html ║
╚══════════════════════════════════════╝
  `);
});

module.exports = app;
