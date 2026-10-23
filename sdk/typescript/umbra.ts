/** Persistent Unix-socket client. Node 24+ can run this TypeScript directly. */
import { createHash, randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join, isAbsolute } from 'node:path';
import { createConnection, type Socket } from 'node:net';
import { spawn } from 'node:child_process';

type Args = Record<string, unknown>;
type ErrorDetails = { code: number | string; message: string; [key: string]: unknown };
export class UmbraError extends Error {
  code: number | string;
  details: ErrorDetails;
  constructor(code: number | string, message: string, details?: ErrorDetails) {
    super(message); this.name = 'UmbraError'; this.code = code;
    this.details = details ?? { code, message };
  }
}
export function profileSocket(profile: string): string {
  if (typeof profile !== 'string' || !profile.trim() || profile.includes('\0')) throw new TypeError('an explicit nonempty profile is required');
  const safe = profile.replaceAll('/', '_');
  const hash = createHash('sha256').update(safe).digest('hex');
  const suffix = [...safe].length > 24 ? `${hash.slice(0, 8)}-${hash.slice(8, 11)}` : safe;
  return join(homedir(), 'Library/Application Support/Umbra', `daemon-${suffix}.sock`);
}
type Options = { binary?: string; socketPath?: string; maxReplyBytes?: number };
type CallOptions = { deadline?: number; requestId?: string; signal?: AbortSignal };
export class Umbra {
  profile: string;
  socketPath: string;
  binary?: string;
  maxReplyBytes: number;
  private socket?: Socket;
  private buffer = Buffer.alloc(0);
  private queue: Promise<unknown> = Promise.resolve();
  private closed = false;
  constructor(profile: string, options: Options = {}) {
    this.socketPath = options.socketPath ?? profileSocket(profile);
    profileSocket(profile);
    this.profile = profile; this.binary = options.binary;
    this.maxReplyBytes = options.maxReplyBytes ?? 16 * 1024 * 1024;
    if (!Number.isSafeInteger(this.maxReplyBytes) || this.maxReplyBytes < 1) throw new RangeError('maxReplyBytes must be positive');
  }
  async start(deadline = Date.now() / 1000 + 20) {
    if (!this.binary || !isAbsolute(this.binary)) throw new TypeError('start requires an explicit absolute Umbra binary path');
    try { await this.call('status', {}, { deadline }); return undefined; }
    catch (error) { if (!(error instanceof UmbraError) || error.code !== 1 || this.closed) throw error; }
    const child = spawn(this.binary, ['serve'], { env: { ...process.env, UMBRA_PROFILE: this.profile }, detached: true, stdio: 'ignore' });
    child.unref();
    let spawnError: Error | undefined;
    child.on('error', error => { spawnError = error; });
    while (Date.now() / 1000 < deadline) {
      if (spawnError) throw new UmbraError(1, spawnError.message);
      try { await this.call('status', {}, { deadline }); return child; }
      catch (error) { if (!(error instanceof UmbraError) || error.code !== 1) throw error; }
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw new UmbraError(2, 'daemon startup deadline elapsed');
  }
  close() { this.closed = true; this.disconnect(); }
  private disconnect() { this.socket?.destroy(); this.socket = undefined; this.buffer = Buffer.alloc(0); }
  call(command: string, args: Args = {}, options: CallOptions = {}): Promise<Args> {
    const deadline = options.deadline ?? Date.now() / 1000 + 30;
    if (!Number.isFinite(deadline)) return Promise.reject(new TypeError('deadline must be finite'));
    if (options.signal?.aborted) return Promise.reject(new UmbraError('cancelled', 'request aborted before dispatch'));
    const requestId = options.requestId ?? randomUUID();
    const runtimeId = args.request ?? requestId;
    if (typeof runtimeId !== 'string') return Promise.reject(new TypeError('request must be a string'));
    const run = () => this.exchange(command, { ...args, request: runtimeId, deadline }, { ...options, deadline, requestId });
    const result = this.queue.then(run, run);
    this.queue = result.catch(() => {});
    let timer: ReturnType<typeof setTimeout>;
    let abort: (() => void) | undefined;
    const queuedDeadline = new Promise<Args>((_, reject) => {
      timer = setTimeout(() => reject(new UmbraError(2, 'request deadline elapsed')), Math.max(0, Math.min(2147483647, deadline * 1000 - Date.now())));
      abort = () => reject(new UmbraError('cancelled', 'request aborted'));
      options.signal?.addEventListener('abort', abort, { once: true });
    });
    return Promise.race([result, queuedDeadline]).finally(() => { clearTimeout(timer); if (abort) options.signal?.removeEventListener('abort', abort); });
  }
  private exchange(command: string, args: Args, options: CallOptions & { deadline: number; requestId: string }): Promise<Args> {
    return new Promise((resolve, reject) => {
      if (this.closed) return reject(new UmbraError(1, 'client is closed'));
      const remaining = options.deadline * 1000 - Date.now();
      if (remaining <= 0) return reject(new UmbraError(2, 'request deadline elapsed while queued'));
      if (options.signal?.aborted) return reject(new UmbraError('cancelled', 'request aborted before dispatch'));
      let payload: string;
      try { payload = JSON.stringify({ id: options.requestId, command, args }) + '\n'; }
      catch (error) { return reject(new UmbraError(64, (error as Error).message)); }
      const socket = this.socket ?? createConnection(this.socketPath);
      if (!this.socket) {
        socket.on('error', () => {});
        socket.on('close', () => { if (this.socket === socket) this.socket = undefined; });
      }
      this.socket = socket;
      let settled = false;
      const clean = () => { clearTimeout(timer); socket.off('data', data); socket.off('error', failed); socket.off('close', ended); options.signal?.removeEventListener('abort', aborted); };
      const finish = (error?: Error, result?: Args) => {
        if (settled) return; settled = true; clean();
        if (error) reject(error); else resolve(result ?? {});
      };
      const failed = (error: Error) => { this.disconnect(); finish(error instanceof UmbraError ? error : new UmbraError(1, error.message)); };
      const ended = () => failed(new UmbraError(1, 'daemon closed the connection'));
      const aborted = () => { if (command !== 'cancel') void this.cancel(args.request as string, { deadline: Date.now() / 1000 + 5, agent: typeof args.agent === 'string' ? args.agent : undefined, task: typeof args.task === 'string' ? args.task : undefined }).catch(() => {}); failed(new UmbraError('cancelled', 'request aborted; cancellation requested')); };
      const data = (chunk: Buffer) => {
        this.buffer = Buffer.concat([this.buffer, chunk]);
        if (this.buffer.length > this.maxReplyBytes) return failed(new UmbraError(1, 'reply exceeds maxReplyBytes'));
        const newline = this.buffer.indexOf(10);
        if (newline < 0) return;
        const line = this.buffer.subarray(0, newline); this.buffer = this.buffer.subarray(newline + 1);
        try {
          const reply = JSON.parse(line.toString('utf8'));
          if (reply.id !== options.requestId || typeof reply.ok !== 'boolean') throw new Error('malformed or mismatched reply');
          if (!reply.ok) {
            if (!reply.error || !['number', 'string'].includes(typeof reply.error.code) || typeof reply.error.message !== 'string') throw new Error('malformed error reply');
            finish(new UmbraError(reply.error.code, reply.error.message, reply.error));
          } else {
            if (!reply.result || Array.isArray(reply.result) || typeof reply.result !== 'object') throw new Error('malformed result reply');
            finish(undefined, reply.result);
          }
        } catch (error) { failed(new UmbraError(1, (error as Error).message)); }
      };
      const timer = setTimeout(() => { if (command !== 'cancel') void this.cancel(args.request as string, { deadline: Date.now() / 1000 + 5, agent: typeof args.agent === 'string' ? args.agent : undefined, task: typeof args.task === 'string' ? args.task : undefined }).catch(() => {}); failed(new UmbraError(2, 'request deadline elapsed')); }, Math.min(2147483647, remaining));
      socket.on('data', data); socket.once('error', failed); socket.once('close', ended);
      options.signal?.addEventListener('abort', aborted, { once: true });
      socket.write(payload);
    });
  }
  /** Fresh observation + provider callback. Returns a proposal; never executes the step. */
  async proposeRepair(step: Args & { command: string; args?: Args }, callback: (observation: Args, step: Args) => Args | Promise<Args>, observeArgs: Args = {}, options: CallOptions = {}) {
    if (typeof step.command !== 'string' || !step.command.trim() || (step.args !== undefined && (!step.args || Array.isArray(step.args) || typeof step.args !== 'object'))) throw new TypeError('step requires a command and object args');
    const deadline = options.deadline ?? Date.now() / 1000 + 30;
    const observation = await this.call('observe', observeArgs, { ...options, deadline });
    const replacement = await callback(observation, JSON.parse(JSON.stringify(step)));
    if (!replacement || Array.isArray(replacement) || typeof replacement !== 'object') throw new TypeError('repair callback must return an args object');
    JSON.stringify(replacement);
    if (Date.now() / 1000 >= deadline) throw new UmbraError(2, 'repair proposal deadline elapsed');
    return { step: { ...step, args: replacement }, observation, dispatched: false };
  }
  async cancel(requestId: string, options: CallOptions & { agent?: string; task?: string } = {}): Promise<Args> {
    const control = new Umbra(this.profile, { socketPath: this.socketPath, maxReplyBytes: this.maxReplyBytes });
    try { return await control.call('cancel', { id: requestId, ...(options.agent !== undefined ? {agent:options.agent} : {}), ...(options.task !== undefined ? {task:options.task} : {}) }, options); }
    finally { control.close(); }
  }
}
