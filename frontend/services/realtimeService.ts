/**
 * Realtime Service - SSE-based realtime updates via FastAPI
 *
 * Replaces direct Supabase Realtime connections.
 * Frontend now only connects to FastAPI backend for all realtime events.
 */

import { getAuthHeaders } from './parserService';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

export interface RealtimeEvent {
  type: 'video' | 'collection_video' | 'video_tag' | 'heartbeat' | 'connected' | 'error';
  event: 'insert' | 'update' | 'delete' | 'ping' | 'open' | 'subscription_failed';
  data: Record<string, any>;
}

export type RealtimeCallback = (event: RealtimeEvent) => void;

export class RealtimeClient {
  private eventSource: EventSource | null = null;
  private callbacks: Map<string, RealtimeCallback[]> = new Map();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private isConnecting = false;

  /**
   * Connect to the SSE endpoint
   */
  async connect(): Promise<void> {
    if (this.eventSource || this.isConnecting) {
      return;
    }

    this.isConnecting = true;

    try {
      const apiUrl = getApiUrl();
      const headers = getAuthHeaders();
      const token = headers['Authorization']?.replace('Bearer ', '');

      if (!token) {
        console.warn('No auth token available for realtime connection');
        this.isConnecting = false;
        return;
      }

      // EventSource doesn't support custom headers, so we pass token as query param
      const url = `${apiUrl}/api/v1/realtime/subscribe?token=${encodeURIComponent(token)}`;

      this.eventSource = new EventSource(url);

      this.eventSource.onopen = () => {
        console.log('Realtime: Connected');
        this.reconnectAttempts = 0;
        this.isConnecting = false;
      };

      this.eventSource.onmessage = (event) => {
        try {
          const data: RealtimeEvent = JSON.parse(event.data);
          this.handleEvent(data);
        } catch (e) {
          console.error('Realtime: Failed to parse event', e);
        }
      };

      this.eventSource.onerror = (error) => {
        console.error('Realtime: Connection error', error);
        this.handleDisconnect();
      };

    } catch (error) {
      console.error('Realtime: Failed to connect', error);
      this.isConnecting = false;
      this.scheduleReconnect();
    }
  }

  /**
   * Disconnect from the SSE endpoint
   */
  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.callbacks.clear();
    this.reconnectAttempts = 0;
    this.isConnecting = false;
    console.log('Realtime: Disconnected');
  }

  /**
   * Subscribe to a specific event type
   */
  on(eventType: RealtimeEvent['type'], callback: RealtimeCallback): () => void {
    if (!this.callbacks.has(eventType)) {
      this.callbacks.set(eventType, []);
    }
    this.callbacks.get(eventType)!.push(callback);

    // Return unsubscribe function
    return () => {
      const callbacks = this.callbacks.get(eventType);
      if (callbacks) {
        const index = callbacks.indexOf(callback);
        if (index > -1) {
          callbacks.splice(index, 1);
        }
      }
    };
  }

  /**
   * Check if connected
   */
  isConnected(): boolean {
    return this.eventSource?.readyState === EventSource.OPEN;
  }

  private handleEvent(event: RealtimeEvent): void {
    // Call type-specific callbacks
    const callbacks = this.callbacks.get(event.type);
    if (callbacks) {
      callbacks.forEach(cb => {
        try {
          cb(event);
        } catch (e) {
          console.error('Realtime: Callback error', e);
        }
      });
    }

    // Log non-heartbeat events
    if (event.type !== 'heartbeat') {
      console.log('Realtime event:', event.type, event.event);
    }
  }

  private handleDisconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isConnecting = false;
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Realtime: Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

    console.log(`Realtime: Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(() => {
      this.connect();
    }, delay);
  }
}

// Global singleton instance
let realtimeClient: RealtimeClient | null = null;

export const getRealtimeClient = (): RealtimeClient => {
  if (!realtimeClient) {
    realtimeClient = new RealtimeClient();
  }
  return realtimeClient;
};

export const connectRealtime = async (): Promise<void> => {
  return getRealtimeClient().connect();
};

export const disconnectRealtime = (): void => {
  if (realtimeClient) {
    realtimeClient.disconnect();
    realtimeClient = null;
  }
};

export const onRealtimeEvent = (
  eventType: RealtimeEvent['type'],
  callback: RealtimeCallback
): (() => void) => {
  return getRealtimeClient().on(eventType, callback);
};
