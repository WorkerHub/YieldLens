import { Hono } from "hono";
import { cors } from "hono/cors";
import { logger } from "hono/logger";
import { stockDetailRoutes } from "./routes/stock-detail";
import { stockRoutes } from "./routes/stocks";
import type { Env, HonoEnv } from "./types";

const app = new Hono<HonoEnv>();

app.use("*", logger());
app.use("*", async (c, next) => {
  const prefix = c.env.TABLE_PREFIX || "";
  if (prefix && !/^[a-zA-Z0-9_]+$/.test(prefix)) {
    return c.json({ error: "Invalid TABLE_PREFIX configuration" }, 500);
  }
  await next();
});
app.use(
  "/api/*",
  cors({
    origin: (origin, c) => {
      if (!origin) return "";
      const url = new URL(c.req.url);
      return origin === url.origin ? origin : "";
    },
    credentials: true,
  }),
);

// Registration order matters: static paths (/search, /industries) must be
// registered before the /:code wildcard route below.
app.route("/api/stocks", stockRoutes);
app.route("/api/stocks", stockDetailRoutes);

app.get("/api/health", (c) => c.json({ status: "ok", timestamp: new Date().toISOString() }));

// SPA fallback — serves the built React app for any non-API route.
app.get("*", async (c) => {
  const res = await c.env.ASSETS.fetch(c.req.raw);
  if (res.status !== 404) return res;
  return c.env.ASSETS.fetch(new Request(new URL("/", c.req.url), c.req.raw));
});

export default {
  fetch: app.fetch,
} satisfies ExportedHandler<Env>;
