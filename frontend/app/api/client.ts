import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

// Relative URLs: the dev server proxies /api to the backend (vite.config.ts).
export const api = createClient<paths>({ baseUrl: "" });

export type MeetingHit = components["schemas"]["MeetingHit"];
export type TopicHit = components["schemas"]["TopicHit"];
export type TextHit = components["schemas"]["TextHit"];
export type MeetingSummary = components["schemas"]["MeetingSummary"];
export type MeetingView = components["schemas"]["MeetingView"];
export type TopicView = components["schemas"]["TopicView"];

export function documentUrl(documentId: number, page?: number | null): string {
  const url = `/api/documents/${documentId}/file`;
  return page ? `${url}#page=${page}` : url;
}

/** Throws a 404/500 Response so the route's ErrorBoundary shows it. */
export function orThrow<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data === undefined) {
    throw new Response(result.response.statusText, { status: result.response.status });
  }
  return result.data;
}
