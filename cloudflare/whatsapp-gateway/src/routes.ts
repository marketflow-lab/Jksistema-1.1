import { createPairingCode, registerBinding, revokeBinding } from "./bindings";
import { bridgeMedia } from "./inbound-media";
import { claimMessages, messageProgress, messageResult, messageTyping } from "./messages";
import { businessProfile, finalizeMetaWebhook, updateProfilePhoto } from "./meta-admin";
import { messageImage, proactiveImage } from "./outbound-media";
import { interactiveApproval, proactive } from "./proactive";
import { bridgeAuthorized, Env, json } from "./shared";
import { bridgeHeartbeat, bridgeStatus } from "./status";
import { syncTemplates } from "./templates";

function removedFeature(): Response {
  return json({ success: false, error: "feature_removed_question_only_mode" }, 410);
}

export async function bridgeRoute(request: Request, env: Env): Promise<Response> {
  if (!bridgeAuthorized(request, env)) return json({ success: false, error: "unauthorized" }, 401);
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/bridge/status") return bridgeStatus(request, env);
  if (request.method === "GET" && url.pathname === "/bridge/meta/profile") return businessProfile(env);
  if (request.method === "GET" && url.pathname === "/bridge/meta/calling/status") return removedFeature();
  if (request.method === "POST" && url.pathname === "/bridge/meta/calling/prepare") return removedFeature();
  if (request.method === "POST" && url.pathname === "/bridge/pairing-codes") return createPairingCode(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/bindings/register") return registerBinding(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/bindings/primary") return removedFeature();
  if (request.method === "POST" && url.pathname === "/bridge/welcome") return removedFeature();
  if (request.method === "POST" && url.pathname === "/bridge/messages/send") return removedFeature();
  if (request.method === "POST" && url.pathname === "/bridge/claim") return claimMessages(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/heartbeat") return bridgeHeartbeat(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/proactive") return proactive(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/proactive/image") return proactiveImage(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/proactive/document") return removedFeature();
  if (request.method === "POST" && url.pathname === "/bridge/interactive") return interactiveApproval(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/templates/sync") return syncTemplates(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/meta/finalize") return finalizeMetaWebhook(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/meta/profile/photo") return updateProfilePhoto(request, env);
  if (request.method === "POST" && url.pathname === "/bridge/bindings/revoke") return revokeBinding(request, env);
  if (url.pathname.startsWith("/bridge/voice/")) return removedFeature();
  const mediaMatch = url.pathname.match(/^\/bridge\/media\/([^/]+)$/);
  if (request.method === "GET" && mediaMatch) return bridgeMedia(request, env, decodeURIComponent(mediaMatch[1]));
  const imageMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/image$/);
  if (request.method === "POST" && imageMatch) return messageImage(request, env, decodeURIComponent(imageMatch[1]));
  const documentMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/document$/);
  if (request.method === "POST" && documentMatch) return removedFeature();
  const typingMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/typing$/);
  if (request.method === "POST" && typingMatch) return messageTyping(request, env, decodeURIComponent(typingMatch[1]));
  const progressMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/progress$/);
  if (request.method === "POST" && progressMatch) return messageProgress(request, env, decodeURIComponent(progressMatch[1]));
  const resultMatch = url.pathname.match(/^\/bridge\/messages\/([^/]+)\/result$/);
  if (request.method === "POST" && resultMatch) return messageResult(request, env, decodeURIComponent(resultMatch[1]));
  return json({ success: false, error: "not_found" }, 404);
}
