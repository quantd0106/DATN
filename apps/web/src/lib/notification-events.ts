export type NotificationStateEvent = {
  source: 'bell' | 'center' | 'calendar';
  readId?: string;
  markAll?: boolean;
  unreadCount?: number;
};

type Listener = (event: NotificationStateEvent) => void;
const listeners = new Set<Listener>();

export function emitNotificationStateChanged(event: NotificationStateEvent): void {
  for (const listener of [...listeners]) {
    listener(event);
  }
}

export function onNotificationStateChanged(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
