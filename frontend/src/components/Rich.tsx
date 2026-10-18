import { Fragment } from "react";

/** Tiny markdown: **bold** and line breaks. Model output only needs that much. */
export function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => (
        <Fragment key={i}>
          {i > 0 && <br />}
          {line.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
            part.startsWith("**") ? <strong key={j}>{part.slice(2, -2)}</strong> : part,
          )}
        </Fragment>
      ))}
    </>
  );
}
