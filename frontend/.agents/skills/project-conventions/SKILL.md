---
name: project-conventions
description: Frontend architecture and code conventions — where logic lives, API types, component props, function parameters, filenames, file organization, braces and types.
user-invocable: false
---

# Project conventions

The frontend is a React Router app in SPA mode (`ssr: false`): route modules load their data in a
`clientLoader` that calls the local API through `openapi-fetch`, and styling is Tailwind. Code lives
under `app/` and is imported through the `~/` alias (`~/lib/format`, no file extension).

## Where code lives

```
app/routes.ts        the route table: URL -> route module
app/routes/          route modules: clientLoader, meta, the page component
app/components/      components used by more than one route
app/lib/<domain>.ts  logic, one module per domain: format.ts, people.ts, search.ts
app/api/             the generated API schema and the typed client
```

- Logic that isn't rendering — parsing, formatting, filtering, sorting — goes in `app/lib/`, where it
  can be unit tested without rendering a route. A route module wires the loader to the page and renders.
- A component used by only one route stays in that route module, below the route's default export.
  Move it to `app/components/` when a second route needs it.

## API types come from the generated schema

- Types for API data are aliases of the generated schema in `app/api/client.ts`, never written by hand:

```ts
export type MeetingView = components["schemas"]["MeetingView"];
```

- A backend change reaches the frontend through `pnpm gen:api`. Where a value set must stay in step with
  the backend, derive the type from the schema so that a new value fails `pnpm typecheck` until it is
  handled (see `Sort` and `SORTS` in `app/lib/search.ts`).
- Values from the URL are untrusted: parse and validate them before use (`Number(params.meetingId)`
  with a `Number.isInteger` check, `parseSearch` for the search query).

## Props: read them from `props`

Prefer `props.name` over destructuring. It keeps each value's origin visible at the use site, and it
stops a long destructure list drifting from the type it came from.

```tsx
// prefer
function ResultCard(props: ResultCardProps) {
  return <h2>{props.hit.title}</h2>;
}

// avoid
function ResultCard({ hit }: ResultCardProps) {
  return <h2>{hit.title}</h2>;
}
```

This applies to every component, route components included: `export default function Meeting(props:
Route.ComponentProps)` reads `props.loaderData`. It earns its keep most visibly where a derived local
echoes a prop's name, and a bare identifier no longer tells you whether the value came from the caller:

```tsx
// `title`, `displayTitle` and the `title=` attribute are three different things
function DocumentLink(props: DocumentLinkProps) {
  const displayTitle = props.title ?? props.meeting.title;

  return (
    <a href={documentUrl(props.meeting.document_id)} title={displayTitle}>
      {displayTitle}
    </a>
  );
}
```

### The one exemption: rest-spread passthrough

Destructure only where it is the mechanism rather than the style: a rest-spread passthrough,
`{ className, ...rest }`. Named props must be kept out of `rest`, which `props` cannot express, and
rebuilding `rest` by hand rots the moment a prop is added to the type. Defaults inside such a
destructure ride along with it — `{ variant = "plain", ...rest }` is fine, because `variant` has to be
excluded from `rest` anyway. Separating `ref` from the rest is part of the same exemption: React 19
passes `ref` as an ordinary prop, so `props.ref` works on its own.

**A default is not on its own a reason to destructure.** With no rest-spread in the component, read the
prop and fall back at the use site:

```tsx
// prefer
function Attendees(props: AttendeesProps) {
  return <AttendeeList attendees={props.attendees} withRoles={props.withRoles ?? false} />;
}

// avoid — `withRoles` has a default, and `attendees` rides along on it
function Attendees({ attendees, withRoles = false }: AttendeesProps) {}
```

Nothing else exempts a component: not a short prop list, and not the absence of locals.

The rule is about components. Route module functions that React Router calls with an args object —
`clientLoader({ params })`, `meta({ loaderData })` — may destructure it.

## Input objects for functions with more than 2 parameters

When a function takes more than 2 parameters, group them into a named `Input` type declared in the same
file:

```ts
type Input = {
  documentId: number;
  page: number | null;
  query: string;
};

export function documentLink(input: Input): string { … }
```

A leading context parameter (a client, a request) stays separate — only the domain arguments are
grouped.

## Filenames

- A file whose main export is a React component is named exactly as the component, `PascalCase.tsx`:
  `app/components/Highlight.tsx` for `Highlight`. Its co-located test follows the file:
  `Highlight.test.tsx`. A file exporting a small family of components takes the family's name.
- **Route modules in `app/routes/` stay lowercase** (`meeting.tsx`, `people.tsx`). They are route
  modules, not components: `app/routes.ts` refers to them by path, and `react-router typegen` names their
  `./+types/<name>` types after the file.
- **Non-component modules are lowercase**: `format.ts`, `people.ts`, `client.ts`. Use a dash for a
  multi-word name (`use-search.ts`).

Renaming across a case-insensitive filesystem hides broken imports: macOS resolves `./highlight` to
`Highlight.tsx` and Linux CI does not. A green `pnpm typecheck` locally is not evidence — check that
every import specifier matches the file's case exactly.

## File organization: important code first

In any file, the exported API (a route's `clientLoader` and default export, a module's exported functions
and types) comes before implementation details such as private helpers and sub-components.

## Always use braces

Always use `{}` with `if`, `else`, `for` and `while`, even for a one-line body.

```ts
// do
if (!isoDate) {
  return "päivämäärä puuttuu";
}

// don't
if (!isoDate) return "päivämäärä puuttuu";
```

## `type`, not `interface`

Declare object types with `type`. The codebase has no interfaces, and the generated API types are `type`
aliases.
