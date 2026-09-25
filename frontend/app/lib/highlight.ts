// The API marks search matches with these control characters (backend search.py). Splitting on them and
// rendering the parts as React text means nothing from a document is ever interpreted as HTML.
const MARK_START = "\u0002";
const MARK_END = "\u0003";

export function highlightParts(text: string): { text: string; match: boolean }[] {
  const parts: { text: string; match: boolean }[] = [];
  for (const [i, segment] of text.split(MARK_START).entries()) {
    const [matched, rest] = i === 0 ? [null, segment] : splitOnce(segment, MARK_END);
    if (matched) {
      parts.push({ text: matched, match: true });
    }
    if (rest) {
      parts.push({ text: rest, match: false });
    }
  }
  return parts;
}

/** The text without markers, e.g. for a page title. */
export function plainText(text: string): string {
  return text.replaceAll(MARK_START, "").replaceAll(MARK_END, "");
}

function splitOnce(text: string, separator: string): [string, string] {
  const index = text.indexOf(separator);
  return index === -1 ? [text, ""] : [text.slice(0, index), text.slice(index + separator.length)];
}
