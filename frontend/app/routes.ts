import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/search.tsx"),
  route("kokoukset", "routes/meetings.tsx"),
  route("kokoukset/:meetingId", "routes/meeting.tsx"),
  route("henkilot", "routes/people.tsx"),
  route("henkilot/:personId", "routes/person.tsx"),
  route("hallitus", "routes/boards.tsx"),
  route("hallitus/:year", "routes/board.tsx"),
] satisfies RouteConfig;
