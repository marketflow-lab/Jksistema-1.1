import { cleanupInboundMedia } from "./inbound-media";
import { Env, nowSeconds } from "./shared";

export async function cleanup(env: Env): Promise<void> {
  const now = nowSeconds();
  await cleanupInboundMedia(env, now);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pairing_codes WHERE expires_at<?").bind(now - 86400),
    env.DB.prepare("DELETE FROM audit_events WHERE created_at<?").bind(now - 30 * 86400),
    env.DB.prepare("DELETE FROM message_status WHERE status_at<?").bind(now - 30 * 86400),
    env.DB.prepare("DELETE FROM outbound_media WHERE created_at<?").bind(now - 90 * 86400),
    env.DB.prepare("DELETE FROM message_progress WHERE created_at<?").bind(now - 30 * 86400),
    env.DB.prepare("DELETE FROM usage_counters WHERE counter_key LIKE 'typing_pulses:%' AND updated_at<?").bind(now - 7 * 86400),
  ]);
}
