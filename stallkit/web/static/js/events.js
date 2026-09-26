// Server-sent events client: one EventSource on /api/events for the whole app.
//
//   const off = events.on("job", (job) => ...);   // -> unsubscribe()
//
// Server topics: hello, status, shop, job, job-event, notification (and any other topic
// a page subscribes to). Client-side topics:
//   "connection" {connected: bool}  - stream opened / lost
//   "reconnect"  {}                 - stream is back after a drop (status was refetched)
// On every reconnect /api/status is fetched again and delivered on "status".

import { api } from "./api.js";

const BASE_TOPICS = ["hello", "status", "shop", "job", "job-event", "notification"];

class EventClient {
  constructor() {
    this.handlers = new Map(); // topic -> Set<cb>
    this.topics = new Set(BASE_TOPICS);
    this.source = null;
    this.attempt = 0;
    this.timer = null;
    this.closed = true;
    this.connected = false;
    this.everConnected = false;
    this.instance = null;
  }

  /** Subscribe; returns an unsubscribe function. */
  on(topic, cb) {
    let set = this.handlers.get(topic);
    if (!set) {
      set = new Set();
      this.handlers.set(topic, set);
    }
    set.add(cb);
    if (!this.topics.has(topic) && !isLocalTopic(topic)) {
      this.topics.add(topic);
      if (this.source) this._listen(this.source, topic);
    }
    return () => {
      set.delete(cb);
    };
  }

  /** Subscribe for one delivery only. */
  once(topic, cb) {
    const off = this.on(topic, (data) => {
      off();
      cb(data);
    });
    return off;
  }

  /** Deliver locally (also used for client-side topics). */
  emit(topic, data) {
    const set = this.handlers.get(topic);
    if (!set) return;
    for (const cb of [...set]) {
      try {
        cb(data);
      } catch (err) {
        console.error(`[events] handler for "${topic}" failed`, err);
      }
    }
  }

  connect() {
    this.closed = false;
    if (!this.lifecycleBound) {
      this.lifecycleBound = true;
      // Leaving the page (or entering the back/forward cache) must not keep the stream
      // open: browsers allow only a few connections per host.
      window.addEventListener("pagehide", () => {
        clearTimeout(this.timer);
        if (this.source) this.source.close();
        this.source = null;
        this._setConnected(false);
      });
      window.addEventListener("pageshow", (e) => {
        if (e.persisted && !this.closed) this._probe();
      });
    }
    this._open();
  }

  /** Stop for good (e.g. after quitting the app). */
  close() {
    this.closed = true;
    clearTimeout(this.timer);
    if (this.source) this.source.close();
    this.source = null;
    this._setConnected(false);
  }

  _listen(source, topic) {
    source.addEventListener(topic, (e) => {
      let data = null;
      try {
        data = e.data ? JSON.parse(e.data) : null;
      } catch {
        data = e.data;
      }
      if (topic === "hello" && data && data.instance) this.instance = data.instance;
      this.emit(topic, data);
    });
  }

  _open() {
    if (this.closed) return;
    let source;
    try {
      source = new EventSource("/api/events");
    } catch (err) {
      console.warn("[events] EventSource failed", err);
      this._scheduleReconnect();
      return;
    }
    this.source = source;
    for (const topic of this.topics) this._listen(source, topic);
    source.onopen = () => {
      const wasReconnect = this.everConnected;
      this.attempt = 0;
      this.everConnected = true;
      this._setConnected(true);
      if (wasReconnect) this.emit("reconnect", {});
    };
    source.onerror = () => {
      // Take reconnecting into our own hands (backoff + session check).
      source.close();
      if (this.source === source) this.source = null;
      this._setConnected(false);
      this._scheduleReconnect();
    };
  }

  _setConnected(value) {
    if (this.connected === value) return;
    this.connected = value;
    this.emit("connection", { connected: value });
  }

  _scheduleReconnect() {
    if (this.closed) return;
    clearTimeout(this.timer);
    const delay = Math.min(15000, 1000 * 2 ** this.attempt) + Math.round(Math.random() * 400);
    this.attempt = Math.min(this.attempt + 1, 6);
    this.timer = setTimeout(() => this._probe(), delay);
  }

  async _probe() {
    if (this.closed) return;
    // A status fetch tells us whether the server is back and our session still valid
    // (401 no_session is handled globally by api.onNoSession).
    try {
      const status = await api.get("/api/status");
      if (this.closed) return;
      this.emit("status", status);
      this._open();
    } catch (err) {
      if (err && err.code === "no_session") {
        this.close();
        return;
      }
      this._scheduleReconnect();
    }
  }
}

function isLocalTopic(topic) {
  return topic === "connection" || topic === "reconnect";
}

export const events = new EventClient();
export default events;
