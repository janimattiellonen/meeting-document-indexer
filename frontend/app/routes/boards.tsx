import { redirect } from "react-router";

import { api, orThrow } from "~/api/client";

/** /hallitus: the latest board. */
export async function clientLoader() {
  const years = orThrow(await api.GET("/api/boards"));
  const latest = years.at(-1);
  if (!latest) throw new Response("Not found", { status: 404 });
  return redirect(`/hallitus/${latest.year}`);
}

export default function Boards() {
  return null;
}
