/** Live updates over WebSocket with exponential reconnect. While the socket
 * is down the app falls back to TanStack Query's polling (refetchInterval),
 * so the dashboard degrades gracefully. */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { API_BASE, getAccessToken } from "../api/client";

export interface WsEvent {
  type: string;
  data: Record<string, unknown>;
  project_id?: string | null;
  at?: string;
}

export function useLiveEvents(onEvent?: (e: WsEvent) => void) {
  const [connected, setConnected] = useState(false);
  const queryClient = useQueryClient();
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let attempts = 0;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      const token = getAccessToken();
      if (!token || closed) return;
      // When API_BASE is absolute (hosted frontend), connect to that host;
      // otherwise use the current origin (local dev proxy).
      const apiUrl = API_BASE.startsWith("http") ? new URL(API_BASE) : null;
      const wsHost = apiUrl ? apiUrl.host : location.host;
      const secure = apiUrl ? apiUrl.protocol === "https:" : location.protocol === "https:";
      const proto = secure ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${wsHost}/api/v1/ws?token=${token}`);
      ws.onopen = () => { attempts = 0; setConnected(true); };
      ws.onmessage = (msg) => {
        try {
          const event: WsEvent = JSON.parse(msg.data);
          if (event.type === "ping") return;
          handlerRef.current?.(event);
          if (event.type.startsWith("job.")) {
            void queryClient.invalidateQueries({ queryKey: ["jobs"] });
            void queryClient.invalidateQueries({ queryKey: ["overview"] });
            void queryClient.invalidateQueries({ queryKey: ["queues"] });
          }
          if (event.type.startsWith("worker.")) {
            void queryClient.invalidateQueries({ queryKey: ["workers"] });
          }
          if (event.type.startsWith("workflow.")) {
            void queryClient.invalidateQueries({ queryKey: ["workflows"] });
          }
          if (event.type.startsWith("queue.")) {
            void queryClient.invalidateQueries({ queryKey: ["queues"] });
          }
        } catch { /* ignore malformed frames */ }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          attempts += 1;
          timer = setTimeout(connect, Math.min(15000, 500 * 2 ** attempts));
        }
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => { closed = true; clearTimeout(timer); ws?.close(); };
  }, [queryClient]);

  return { connected };
}
