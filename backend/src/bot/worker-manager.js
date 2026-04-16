// backend/src/bot/worker-manager.js
// Manages the pool of Worker threads (one per account)
// Emits real-time events to connected SSE clients

const { Worker } = require('worker_threads');
const path = require('path');
const { EventEmitter } = require('events');

const WORKER_SCRIPT = path.join(__dirname, 'worker.js');

class WorkerManager extends EventEmitter {
  constructor() {
    super();
    this.workers = new Map(); // accountId → Worker instance
    this.MAX_CONCURRENT = parseInt(process.env.MAX_CONCURRENT_WORKERS) || 15;
  }

  /**
   * Start bot for a single account
   */
  start(accountId) {
    if (this.workers.has(accountId)) {
      throw new Error(`Worker for account ${accountId} is already running`);
    }
    if (this.workers.size >= this.MAX_CONCURRENT) {
      throw new Error(`Max concurrent workers (${this.MAX_CONCURRENT}) reached`);
    }

    const worker = new Worker(WORKER_SCRIPT, {
      workerData: { accountId },
      // Share the event loop but isolate execution
      resourceLimits: { maxOldGenerationSizeMb: 512 },
    });

    worker.on('message', (msg) => {
      this.emit('worker:message', msg);
    });

    worker.on('error', (err) => {
      console.error(`Worker error [${accountId}]:`, err);
      this.emit('worker:message', {
        type: 'status',
        accountId,
        payload: { status: 'error', message: err.message },
      });
      this.workers.delete(accountId);
    });

    worker.on('exit', (code) => {
      console.log(`Worker [${accountId}] exited with code ${code}`);
      this.workers.delete(accountId);
      this.emit('worker:message', {
        type: 'exit',
        accountId,
        payload: { code },
      });
    });

    this.workers.set(accountId, worker);
    return worker;
  }

  /**
   * Start bots for multiple accounts concurrently
   */
  startMany(accountIds) {
    const results = [];
    for (const id of accountIds) {
      try {
        this.start(id);
        results.push({ accountId: id, started: true });
      } catch (err) {
        results.push({ accountId: id, started: false, error: err.message });
      }
    }
    return results;
  }

  /**
   * Stop a specific worker
   */
  async stop(accountId) {
    const worker = this.workers.get(accountId);
    if (!worker) return false;
    await worker.terminate();
    this.workers.delete(accountId);
    return true;
  }

  /**
   * Stop all workers
   */
  async stopAll() {
    const promises = [...this.workers.keys()].map((id) => this.stop(id));
    await Promise.all(promises);
  }

  /**
   * Returns list of currently running account IDs
   */
  getRunning() {
    return [...this.workers.keys()];
  }

  isRunning(accountId) {
    return this.workers.has(accountId);
  }
}

// Singleton
const manager = new WorkerManager();
module.exports = manager;
