/** One dispatched Server-Sent Event. */
export type SseMessage = {
  /** The `event:` field, or "message" when the event named none. */
  event: string;
  /** The `data:` lines joined with "\n". */
  data: string;
  /** The last event id seen on the stream so far (it carries over between events), or null. */
  id: string | null;
};

export type SseHandlers = {
  onMessage: (msg: SseMessage) => void;
  /** A comment line (`: ping`), without the colon and one leading space. */
  onComment?: (text: string) => void;
  /** A `retry:` field: the reconnection delay the server asks for, in ms. */
  onRetry?: (ms: number) => void;
};

const LF = 10;
const CR = 13;

/**
 * An incremental text/event-stream parser following the WHATWG event stream rules. Feed it
 * decoded text in pieces of any size as it arrives: lines may end in CRLF, LF or CR, and a
 * CRLF may be split across two pieces. An event is dispatched at the blank line that ends it;
 * whatever is pending when the stream ends is discarded, as the standard says.
 */
export class SseParser {
  private tail = "";
  private data = "";
  private hasData = false;
  private type = "";
  private lastId: string | null = null;
  /** The previous piece ended in CR: an LF at the start of the next one belongs to it. */
  private pendingCR = false;
  private started = false;

  constructor(private readonly handlers: SseHandlers) {}

  /** The id of the last event that carried one. */
  get lastEventId(): string | null {
    return this.lastId;
  }

  feed(text: string): void {
    if (!text) return;
    if (!this.started) {
      this.started = true;
      if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    }
    if (this.pendingCR) {
      this.pendingCR = false;
      if (text.charCodeAt(0) === LF) text = text.slice(1);
    }
    const buf = this.tail + text;
    let start = 0;
    for (let i = 0; i < buf.length; i++) {
      const c = buf.charCodeAt(i);
      if (c !== LF && c !== CR) continue;
      this.line(buf.slice(start, i));
      if (c === CR) {
        if (i + 1 === buf.length) this.pendingCR = true;
        else if (buf.charCodeAt(i + 1) === LF) i++;
      }
      start = i + 1;
    }
    this.tail = buf.slice(start);
  }

  /** The stream ended: an unfinished line or event is dropped. */
  end(): void {
    this.tail = "";
    this.data = "";
    this.hasData = false;
    this.type = "";
    this.pendingCR = false;
  }

  private line(line: string): void {
    if (line === "") return this.dispatch();
    if (line.charCodeAt(0) === 58 /* : */) {
      const text = line.slice(1);
      this.handlers.onComment?.(text.startsWith(" ") ? text.slice(1) : text);
      return;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.type = value;
        break;
      case "data":
        this.data = this.hasData ? `${this.data}\n${value}` : value;
        this.hasData = true;
        break;
      case "id":
        if (!value.includes("\0")) this.lastId = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.handlers.onRetry?.(Number(value));
        break;
      default:
        break; // unknown fields are ignored
    }
  }

  private dispatch(): void {
    if (!this.hasData) {
      this.type = "";
      return;
    }
    const msg: SseMessage = { event: this.type || "message", data: this.data, id: this.lastId };
    this.data = "";
    this.hasData = false;
    this.type = "";
    this.handlers.onMessage(msg);
  }
}
