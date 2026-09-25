import { highlightParts } from "~/lib/highlight";

type HighlightProps = {
  text: string;
};

export function Highlight(props: HighlightProps) {
  return (
    <>
      {highlightParts(props.text).map((part, i) =>
        part.match ? (
          <mark key={i} className="rounded-sm bg-amber-200 px-0.5 text-inherit dark:bg-amber-500/40">
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}
