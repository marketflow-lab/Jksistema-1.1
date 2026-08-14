import { flushOutbox } from "./delivery";
import { retryInboundMedia } from "./inbound-media";
import { cleanup } from "./maintenance";
import { bridgeRoute } from "./routes";
import { Env, GATEWAY_BUILD_VERSION, GATEWAY_PROTOCOL_VERSION, json } from "./shared";
import { handleOpenAIRealtimeWebhook } from "./voice";
import { handleWebhook } from "./webhook";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === "/webhooks/whatsapp") return handleWebhook(request, env, ctx);
    if (path === "/webhooks/openai/realtime") return handleOpenAIRealtimeWebhook(request, env);
    if (path.startsWith("/bridge/")) return bridgeRoute(request, env);
    return json({
      success: true,
      service: "jk-whatsapp-gateway",
      zero_cost: true,
      voice_realtime_sip: true,
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
