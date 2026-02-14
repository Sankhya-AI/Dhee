import { useCallback, useEffect, useRef, useState } from "react";

export type ConnectionStatus = "connecting" | "connected" | "disconnected";

export interface WsMessage {
  type: string;
  message_id?: number;
  content?: string;
  user_id?: number;
  filename?: string;
  content_b64?: string;
}

interface UseWebSocketOptions {
  onMessage: (msg: WsMessage) => void;
}

export function useWebSocket({ onMessage }: UseWebSocketOptions) {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [userId, setUserId] = useState<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pingTimer = useRef<ReturnType<typeof setInterval>>(undefined);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const getWsUrl = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token") || "";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = `${proto}//${window.location.host}/ws`;
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setStatus("connecting");
    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      // Ping keepalive every 30s
      pingTimer.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const data: WsMessage = JSON.parse(event.data);
        if (data.type === "connected" && data.user_id) {
          setUserId(data.user_id);
        }
        if (data.type === "pong") return;
        onMessageRef.current(data);
      } catch {
        // ignore non-JSON
      }
    };

    ws.onclose = () => {
      setStatus("disconnected");
      if (pingTimer.current) clearInterval(pingTimer.current);
      // Auto-reconnect after 3s
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [getWsUrl]);

  const disconnect = useCallback(() => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    if (pingTimer.current) clearInterval(pingTimer.current);
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent auto-reconnect
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("disconnected");
  }, []);

  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);

  return { status, userId, send, reconnect: connect };
}
