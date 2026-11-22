// Availability is presentation only. Route and server authorization still apply.
const liveUnavailable = new Set([
  'a.reports', 'a.dataRequests',
  's.progress', 't.analytics',
]);

export function isRouteAvailable(id: string, source: 'live' | 'mock') {
  return source === 'mock' || !liveUnavailable.has(id);
}
