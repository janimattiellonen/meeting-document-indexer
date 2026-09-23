import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/search.tsx"),
  route("kokoukset", "routes/meetings.tsx"),
  route("kokoukset/:meetingId", "routes/meeting.tsx"),
] satisfies RouteConfig;
