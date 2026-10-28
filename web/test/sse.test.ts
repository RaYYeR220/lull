import { describe, expect, it } from "vitest";
import { SseParser, type SseMessage } from "../lib/sse";

function parse(chunks: string[]) {
  const messages: SseMessage[] = [];
  const comments: string[] = [];
  const retries: number[] = [];
  const p = new SseParser({ onMessage: (m) => messages.push(m), onComment: (c) => comments.push(c), onRetry: (r) => retries.push(r) });
  for (const c of chunks) p.feed(c);
  p.end();
  return { messages, comments, retries, parser: p };
}

/** Every way to cut `text` in two. */
function splits(text: string): string[][] {
  return Array.from({ length: text.length + 1 }, (_, i) => [text.slice(0, i), text.slice(i)]);
}

const STREAM =
  'id: 0\nevent: run\ndata: {"type":"run","run_id":"live-1","seq":0}\n\n' +
  ": ping\n\n" +
  'id: 1\r\nevent: stage_start\r\ndata: {"type":"stage_start","stage":"listen","seq":1}\r\n\r\n' +
  'id: 2\revent: cue\rdata: {"type":"cue","id":"c001",\rdata: "text":"A man waits."}\r\r';

describe("SseParser", () => {
  it("reads events with their type, data and id", () => {
    const { messages, comments } = parse([STREAM]);
    expect(messages.map((m) => [m.event, m.id])).toEqual([
      ["run", "0"],
      ["stage_start", "1"],
      ["cue", "2"],
    ]);
    expect(JSON.parse(messages[0]!.data)).toEqual({ type: "run", run_id: "live-1", seq: 0 });
    expect(messages[2]!.data).toBe('{"type":"cue","id":"c001",\n"text":"A man waits."}');
    expect(comments).toEqual(["ping"]);
  });

  it("gives the same events however the stream is cut into pieces", () => {
    const whole = parse([STREAM]).messages;
    for (const pieces of splits(STREAM)) expect(parse(pieces).messages).toEqual(whole);
    expect(parse([...STREAM]).messages).toEqual(whole); // one character at a time
  });

  it("joins a CRLF split across two pieces, so no blank line appears in between", () => {
    const { messages } = parse(["data: a\r", "\ndata: b\r", "\n\r", "\n"]);
    expect(messages).toEqual([{ event: "message", data: "a\nb", id: null }]);
  });

  it("dispatches only at a blank line and drops an unfinished event at the end", () => {
    expect(parse(["data: done\n\ndata: cut"]).messages.map((m) => m.data)).toEqual(["done"]);
    expect(parse(["data: no blank line\n"]).messages).toEqual([]);
  });

  it("strips one space after the colon and keeps the rest", () => {
    expect(parse(["data:  two\n\ndata:none\n\n"]).messages.map((m) => m.data)).toEqual([" two", "none"]);
  });

  it("dispatches an empty data line but not an event without data", () => {
    const { messages } = parse(["event: x\n\ndata\n\nevent: y\ndata:\n\n"]);
    expect(messages).toEqual([
      { event: "message", data: "", id: null },
      { event: "y", data: "", id: null },
    ]);
  });

  it("carries the last id over to later events and ignores ids with NUL", () => {
    const { messages, parser } = parse(["id: 7\ndata: a\n\ndata: b\n\nid: 8\0\ndata: c\n\n"]);
    expect(messages.map((m) => m.id)).toEqual(["7", "7", "7"]);
    expect(parser.lastEventId).toBe("7");
  });

  it("skips a byte order mark, reports retry, ignores unknown fields", () => {
    const { messages, retries } = parse(["﻿retry: 3000\nretry: soon\nfoo: bar\ndata: x\n\n"]);
    expect(messages.map((m) => m.data)).toEqual(["x"]);
    expect(retries).toEqual([3000]);
  });

  it("resets the event type after each dispatch", () => {
    const { messages } = parse(["event: done\ndata: 1\n\ndata: 2\n\n"]);
    expect(messages.map((m) => m.event)).toEqual(["done", "message"]);
  });
});
