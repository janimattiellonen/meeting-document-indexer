import type { MeetingType } from "~/api/client";

const MEETING_TYPES: Record<MeetingType, string> = {
  board: "Hallituksen kokous",
  spring_general: "Kevätkokous",
  autumn_general: "Syyskokous",
  extraordinary: "Ylimääräinen kokous",
  other: "Yhdistyksen kokous",
};

export function meetingTypeLabel(type: MeetingType): string {
  return MEETING_TYPES[type];
}

/** "2026-05-12" -> "12.5.2026". Parsed by hand: `new Date("2026-05-12")` is UTC and can shift a day. */
export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "päivämäärä puuttuu";
  const [year, month, day] = isoDate.split("-").map(Number);
  return `${day}.${month}.${year}`;
}

/** "18:05:00" -> "18.05" */
export function formatTime(isoTime: string | null | undefined): string | null {
  if (!isoTime) return null;
  return isoTime.slice(0, 5).replace(":", ".");
}

export function yearOf(isoDate: string | null | undefined): string {
  return isoDate ? isoDate.slice(0, 4) : "Ei päivämäärää";
}
