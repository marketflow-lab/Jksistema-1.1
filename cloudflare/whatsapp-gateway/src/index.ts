import { flushOutbox } from "./delivery";
import { retryInboundMedia } from "./inbound-media";
import { cleanup } from "./maintenance";
import { bridgeRoute } from "./routes";
import { Env, GATEWAY_BUILD_VERSION, GATEWAY_PROTOCOL_VERSION, json } from "./shared";
import { handleWebhook } from "./webhook";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === "/webhooks/whatsapp") return handleWebhook(request, env, ctx);
    if (path === "/webhooks/openai/realtime") {
      return json({ success: false, error: "feature_removed_question_only_mode" }, 410);
    }
    if (path.startsWith("/bridge/")) return bridgeRoute(request, env);
    return json({
      success: true,
      service: "jk-whatsapp-gateway",
      zero_cost: true,
      channel_mode: "question_replies_only_v1",
      gateway_protocol_version: GATEWAY_PROTOCOL_VERSION,
      build_version: GATEWAY_BUILD_VERSION,
    });
  },
  async scheduled(_controller: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<void> {
    await retryInboundMedia(env);
    await flushOutbox(env, "", 10);
    await cleanup(env);
  },
};
