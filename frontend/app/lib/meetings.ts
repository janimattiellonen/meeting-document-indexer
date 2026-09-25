import type { MeetingSummary } from "~/api/client";
import { yearOf } from "~/lib/format";

type Input = {
  meetingId: number;
  page?: number | null;
  topicId?: number;
};

/** Meetings grouped by year, newest year first (the API returns them newest first). */
export function byYear(meetings: MeetingSummary[]): [string, MeetingSummary[]][] {
  const groups = new Map<string, MeetingSummary[]>();
  for (const meeting of meetings) {
    const year = yearOf(meeting.meeting_date);
    groups.set(year, [...(groups.get(year) ?? []), meeting]);
  }
  return [...groups];
}

/** The meeting page, opened at a document page (?sivu=…) and with a topic selected (?kohta=…). */
export function meetingUrl(input: Input): string {
  const params = new URLSearchParams();
  if (input.page) {
    params.set("sivu", String(input.page));
  }
  if (input.topicId) {
    params.set("kohta", String(input.topicId));
  }
  const query = params.toString();
  return `/kokoukset/${input.meetingId}${query ? `?${query}` : ""}`;
}

/** The page and topic in a meeting URL (see `meetingUrl`); anything but a positive integer is ignored. */
export function parseMeetingParams(params: URLSearchParams): { page: number | null; topicId: number | null } {
  return {
    page: parsePositiveInteger(params.get("sivu")),
    topicId: parsePositiveInteger(params.get("kohta")),
  };
}

function parsePositiveInteger(value: string | null): number | null {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}
