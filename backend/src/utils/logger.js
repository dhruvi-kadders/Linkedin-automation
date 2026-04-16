// backend/src/utils/logger.js
// Structured logger — writes to console + PostgreSQL bot_logs table

const { logs } = require('../db');

class Logger {
  constructor(accountId, label = '') {
    this.accountId = accountId;
    this.label = label;
  }

  _format(level, message, meta) {
    const ts = new Date().toISOString();
    const prefix = this.label ? `[${this.label}]` : '';
    console.log(`${ts} ${level.toUpperCase()} ${prefix} ${message}`, meta || '');
  }

  async _persist(level, message, meta) {
    try {
      await logs.insert(this.accountId, level, message, meta);
    } catch (_) { /* non-fatal */ }
  }

  info(message, meta) {
    this._format('info', message, meta);
    this._persist('info', message, meta);
  }

  warn(message, meta) {
    this._format('warn', message, meta);
    this._persist('warn', message, meta);
  }

  error(message, meta) {
    this._format('error', message, meta);
    this._persist('error', message, meta);
  }

  debug(message, meta) {
    this._format('debug', message, meta);
    // debug not persisted to DB to reduce noise
  }
}

module.exports = Logger;
